#include "EmailSyncActivity.h"

#include <GfxRenderer.h>
#include <HalStorage.h>
#include <I18n.h>
#include <Logging.h>
#include <WiFi.h>
#include <ZipFile.h>

#include <algorithm>
#include <cctype>
#include <cstring>

#include "EmailSyncCredentialStore.h"
#include "MappedInputManager.h"
#include "SilentRestart.h"
#include "activities/ActivityManager.h"
#include "activities/network/WifiSelectionActivity.h"
#include "components/TouchHeaderBackButton.h"
#include "components/UITheme.h"
#include "fontIds.h"
#include "network/HttpDownloader.h"

namespace {

constexpr size_t EMAIL_SYNC_DOWNLOAD_BUFFER = 2048;
constexpr int DOWNLOAD_PROGRESS_STEP_PERCENT = 5;

std::string basenameOnly(const std::string& name) {
  const size_t slash = name.find_last_of("/\\");
  std::string base = slash == std::string::npos ? name : name.substr(slash + 1);
  // Collapse CR/LF/tabs from MIME-folded names; FAT rejects control chars and
  // a CR/LF in Content-Disposition also breaks the HTTP response headers.
  std::string cleaned;
  cleaned.reserve(base.size());
  bool pendingSpace = false;
  for (unsigned char ch : base) {
    if (ch < 0x20 || ch == 0x7f || ch == '<' || ch == '>' || ch == ':' || ch == '"' || ch == '|' || ch == '?' ||
        ch == '*') {
      pendingSpace = !cleaned.empty();
      continue;
    }
    if (ch == ' ') {
      pendingSpace = !cleaned.empty();
      continue;
    }
    if (pendingSpace) {
      cleaned.push_back(' ');
      pendingSpace = false;
    }
    cleaned.push_back(static_cast<char>(ch));
  }
  while (!cleaned.empty() && (cleaned.back() == ' ' || cleaned.back() == '.')) cleaned.pop_back();
  return cleaned;
}

bool hasEpubExtension(const std::string& filename) {
  if (filename.size() < 5) return false;
  const char* ext = filename.c_str() + filename.size() - 5;
  return std::tolower(static_cast<unsigned char>(ext[0])) == '.' &&
         std::tolower(static_cast<unsigned char>(ext[1])) == 'e' &&
         std::tolower(static_cast<unsigned char>(ext[2])) == 'p' &&
         std::tolower(static_cast<unsigned char>(ext[3])) == 'u' &&
         std::tolower(static_cast<unsigned char>(ext[4])) == 'b';
}

bool ensureDownloadFolder(const std::string& folder) {
  if (folder.empty()) return false;
  return Storage.ensureDirectoryExists(folder.c_str());
}

std::string joinPath(const std::string& folder, const std::string& file) {
  if (folder.empty()) return file;
  if (folder.back() == '/') return folder + file;
  return folder + "/" + file;
}

}  // namespace

EmailSyncActivity::EmailSyncActivity(GfxRenderer& renderer, MappedInputManager& mappedInput,
                                     const bool networkBootReady)
    : Activity(NAME, renderer, mappedInput), networkBootReady(networkBootReady) {}

void EmailSyncActivity::onEnter() {
  Activity::onEnter();
  EMAIL_SYNC_STORE.ensureLoaded();

  if (!EMAIL_SYNC_STORE.hasCredentials()) {
    state = NO_CREDENTIALS;
    statusMessage = tr(STR_EMAIL_SYNC_SETUP_HINT);
    requestUpdate();
    return;
  }

#ifndef SIMULATOR
  if (!networkBootReady) {
    silentRestartToNetwork(NetworkBootTarget::EMAIL_SYNC);
    return;
  }
#endif

  wifiActivated = true;
  if (WiFi.status() == WL_CONNECTED) {
    onWifiSelectionComplete(true);
    return;
  }

  state = WIFI_SELECTION;
  requestUpdate();
  startActivityForResult(std::make_unique<WifiSelectionActivity>(renderer, mappedInput, true, true),
                         [this](const ActivityResult& result) { onWifiSelectionComplete(!result.isCancelled); });
}

void EmailSyncActivity::onExit() {
  Activity::onExit();
#ifndef SIMULATOR
  if (wifiActivated && WiFi.getMode() != WIFI_MODE_NULL) {
    silentRestart();
  }
#endif
}

void EmailSyncActivity::onWifiSelectionComplete(const bool success) {
  if (!success) {
    state = FAILED;
    statusMessage = tr(STR_WIFI_CONN_FAILED);
    requestUpdate();
    return;
  }
  performSync();
}

void EmailSyncActivity::performSync() {
  state = SYNCING;
  statusMessage = tr(STR_EMAIL_SYNC_CHECKING);
  detailMessage.clear();
  cancelDownload = false;
  completedCount = 0;
  totalCount = 0;
  requestUpdate();

  pendingItems.clear();
  const auto listError = EmailSyncClient::fetchPending(pendingItems);
  if (listError != EmailSyncClient::OK) {
    state = FAILED;
    statusMessage = EmailSyncClient::errorString(listError);
    requestUpdate();
    return;
  }

  totalCount = pendingItems.size();
  if (totalCount == 0) {
    state = SUCCESS;
    statusMessage = tr(STR_EMAIL_SYNC_NOTHING_NEW);
    requestUpdate();
    return;
  }

  const std::string folder = EMAIL_SYNC_STORE.getDownloadFolder();
  if (!ensureDownloadFolder(folder)) {
    state = FAILED;
    statusMessage = tr(STR_EMAIL_SYNC_FOLDER_FAILED);
    requestUpdate();
    return;
  }

  const std::string token = EMAIL_SYNC_STORE.getToken();
  const std::string baseUrl = EMAIL_SYNC_STORE.getBaseUrl();

  for (size_t i = 0; i < pendingItems.size(); ++i) {
    if (cancelDownload) {
      state = FAILED;
      statusMessage = tr(STR_NEARBY_TRANSFER_CANCELLED);
      requestUpdate();
      return;
    }

    const auto& item = pendingItems[i];
    const std::string safeName = basenameOnly(item.filename);
    if (safeName.empty()) continue;

    const std::string destPath = joinPath(folder, safeName);
    state = DOWNLOADING;
    downloadProgress = 0;
    downloadTotal = item.bytes;
    char progressLabel[96];
    snprintf(progressLabel, sizeof(progressLabel), tr(STR_EMAIL_SYNC_DOWNLOADING_FORMAT),
             static_cast<unsigned>(i + 1), static_cast<unsigned>(totalCount), safeName.c_str());
    statusMessage = progressLabel;
    detailMessage.clear();
    requestUpdate();

    HttpDownloader::DownloadOptions options;
    options.shouldCancel = [this]() {
      mappedInput.update();
      if (mappedInput.wasPressed(MappedInputManager::Button::Back) ||
          TouchHeaderBackButton::wasTapped(mappedInput, renderer)) {
        cancelDownload = true;
      }
      return cancelDownload;
    };
    options.bufferSize = EMAIL_SYNC_DOWNLOAD_BUFFER;
    options.transport = HttpDownloader::Transport::WOLFSSL;
    options.authorizationOrigin = baseUrl;
    options.bearerToken = token;
    options.stageAsPart = true;
    options.checkFreeSpace = true;
    if (hasEpubExtension(safeName) && ESP.getFreeHeap() > 40000) {
      options.validate = [](const std::string& path) {
        ZipFile zip(path);
        size_t size = 0;
        return zip.getInflatedFileSize("META-INF/container.xml", &size) && size > 0;
      };
    }

    int lastRenderedPercent = -1;
    const auto result = HttpDownloader::downloadToFile(
        EmailSyncClient::contentUrl(item.id), destPath,
        [this, &lastRenderedPercent](const size_t downloaded, const size_t total) {
          downloadProgress = downloaded;
          downloadTotal = total;
          const int percent = total > 0 ? static_cast<int>(static_cast<uint64_t>(downloaded) * 100 / total) : 0;
          if (percent >= 100 || lastRenderedPercent < 0 || percent >= lastRenderedPercent + DOWNLOAD_PROGRESS_STEP_PERCENT) {
            lastRenderedPercent = percent;
            requestUpdate();
          }
        },
        &cancelDownload, "", "", options);

    if (result == HttpDownloader::ABORTED || cancelDownload) {
      state = FAILED;
      statusMessage = tr(STR_NEARBY_TRANSFER_CANCELLED);
      requestUpdate();
      return;
    }
    if (result != HttpDownloader::OK) {
      state = FAILED;
      statusMessage =
          result == HttpDownloader::INSUFFICIENT_SPACE ? tr(STR_SD_CARD_FULL) : tr(STR_DOWNLOAD_FAILED);
      requestUpdate();
      return;
    }

    const auto ackError = EmailSyncClient::ack(item.id);
    if (ackError != EmailSyncClient::OK) {
      LOG_ERR("EmailSync", "Ack failed for %s: %d", item.id.c_str(), static_cast<int>(ackError));
      // File is on SD; leave pending so a retry can re-download or re-ack.
      state = FAILED;
      statusMessage = EmailSyncClient::errorString(ackError);
      requestUpdate();
      return;
    }
    ++completedCount;
  }

  state = SUCCESS;
  char doneLabel[96];
  snprintf(doneLabel, sizeof(doneLabel), tr(STR_EMAIL_SYNC_DONE_FORMAT), static_cast<unsigned>(completedCount));
  statusMessage = doneLabel;
  requestUpdate();
}

void EmailSyncActivity::exitFlow() {
  mappedInput.suppressNextBackRelease();
  finish();
}

void EmailSyncActivity::loop() {
  if (state == DOWNLOADING) return;  // Cancel polled inside download callback.

  if (TouchHeaderBackButton::wasTapped(mappedInput, renderer) ||
      mappedInput.wasPressed(MappedInputManager::Button::Back) ||
      mappedInput.wasReleased(MappedInputManager::Button::Confirm)) {
    if (state == SUCCESS || state == FAILED || state == NO_CREDENTIALS) {
      exitFlow();
    }
  }
}

void EmailSyncActivity::render(RenderLock&&) {
  const auto& metrics = UITheme::getInstance().getMetrics();
  const auto pageWidth = renderer.getScreenWidth();
  const auto pageHeight = renderer.getScreenHeight();

  renderer.clearScreen();
  const Rect header = TouchHeaderBackButton::headerRect(renderer, mappedInput);
  if (mappedInput.hasTouchHardware()) {
    TouchHeaderBackButton::draw(renderer, header, tr(STR_EMAIL_SYNC), false);
  } else {
    GUI.drawHeader(renderer, header, tr(STR_EMAIL_SYNC));
  }

  const int centerY = pageHeight / 2;
  renderer.drawCenteredText(UI_10_FONT_ID, centerY - metrics.verticalSpacing, statusMessage.c_str(), true,
                            EpdFontFamily::BOLD);

  if (state == DOWNLOADING && downloadTotal > 0) {
    const int percent = static_cast<int>(static_cast<uint64_t>(downloadProgress) * 100 / downloadTotal);
    char pct[32];
    snprintf(pct, sizeof(pct), "%d%%", percent);
    renderer.drawCenteredText(UI_10_FONT_ID, centerY + metrics.verticalSpacing, pct, true);

    const int barWidth = std::min(pageWidth - metrics.contentSidePadding * 2, 360);
    const int barX = (pageWidth - barWidth) / 2;
    const int barY = centerY + metrics.verticalSpacing * 2 + renderer.getLineHeight(UI_10_FONT_ID);
    const int filled = barWidth * percent / 100;
    renderer.drawRect(barX, barY, barWidth, metrics.progressBarHeight, true);
    if (filled > 0) {
      renderer.fillRect(barX, barY, filled, metrics.progressBarHeight, true);
    }
  }

  const char* hint =
      (state == SUCCESS || state == FAILED || state == NO_CREDENTIALS) ? tr(STR_BACK) : tr(STR_LOADING);
  const auto labels = mappedInput.mapLabels(mappedInput.withBackArrow(hint), "", "", "");
  GUI.drawButtonHints(renderer, labels.btn1, labels.btn2, labels.btn3, labels.btn4);
  renderer.displayBuffer(screenTransitionRefresh.modeFor(static_cast<uint8_t>(state)));
}
