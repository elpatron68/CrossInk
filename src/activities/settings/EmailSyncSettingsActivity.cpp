#include "EmailSyncSettingsActivity.h"

#include <GfxRenderer.h>
#include <I18n.h>

#include <cstring>
#include <string>
#include <vector>

#include "EmailSyncCredentialStore.h"
#include "MappedInputManager.h"
#include "SilentRestart.h"
#include "activities/util/KeyboardEntryActivity.h"
#include "components/TouchHeaderBackButton.h"
#include "components/UITheme.h"
#include "components/UIThemeTokens.h"
#include "components/UiAppHelpers.h"
#include "fontIds.h"
#include "util/InputReleaseGuard.h"

namespace fui = freeink::ui;

namespace {
constexpr int MENU_ITEMS = 4;
const StrId menuNames[MENU_ITEMS] = {StrId::STR_EMAIL_SYNC_BASE_URL, StrId::STR_EMAIL_SYNC_TOKEN,
                                     StrId::STR_EMAIL_SYNC_FOLDER, StrId::STR_EMAIL_SYNC_NOW};
constexpr fui::ActionId ACTION_ROW = 1;
}  // namespace

EmailSyncSettingsActivity::EmailSyncSettingsActivity(GfxRenderer& renderer, MappedInputManager& mappedInput)
    : Activity("EmailSyncSettings", renderer, mappedInput),
      uiTarget(makeUiTarget(renderer)),
      app(uiTarget, uiTarget.deviceContext()) {}

void EmailSyncSettingsActivity::onRowEvent(const fui::ActionEvent& event, void* user) {
  auto* self = static_cast<EmailSyncSettingsActivity*>(user);
  if (event.value < 0 || event.value >= MENU_ITEMS) return;
  self->selectedIndex = static_cast<size_t>(event.value);
  self->app.clearTapFlash();
  self->handleSelection();
}

void EmailSyncSettingsActivity::onEnter() {
  Activity::onEnter();
  ignoreInitialConfirmRelease = mappedInput.isPressed(MappedInputManager::Button::Confirm);
  selectedIndex = 0;
  uiReady = false;
  visibleRows = 1;
  topIndex = 0;
  applySharedUiTheme(app, uiTarget);
  app.on(ACTION_ROW, &EmailSyncSettingsActivity::onRowEvent, this);
  app.setScreen(&EmailSyncSettingsActivity::listScreen, this);
  EMAIL_SYNC_STORE.ensureLoaded();
  requestUpdate();
}

void EmailSyncSettingsActivity::onExit() { Activity::onExit(); }

void EmailSyncSettingsActivity::loop() {
  if (InputReleaseGuard::consumeInitialRelease(mappedInput, MappedInputManager::Button::Confirm,
                                               ignoreInitialConfirmRelease)) {
    return;
  }

  if (TouchHeaderBackButton::wasTapped(mappedInput, renderer) ||
      mappedInput.wasPressed(MappedInputManager::Button::Back)) {
    finishAfterBackPress();
    return;
  }

  if (mappedInput.wasReleased(MappedInputManager::Button::Confirm)) {
    handleSelection();
    return;
  }

  if (uiReady) {
    const fui::InputSnapshot snap = touchSnapshotFrom(mappedInput);
    if (snap.touchPressed || snap.touchReleased) {
      const auto event = app.route(snap);
      if (app.invalidated()) requestUpdate();
      if (event) return;
    }
  }

  buttonNavigator.onNext([this] {
    selectedIndex = (selectedIndex + 1) % MENU_ITEMS;
    topIndex = followListSelection(static_cast<int>(selectedIndex), topIndex, visibleRows, MENU_ITEMS);
    requestUpdate();
  });

  buttonNavigator.onPrevious([this] {
    selectedIndex = (selectedIndex + MENU_ITEMS - 1) % MENU_ITEMS;
    topIndex = followListSelection(static_cast<int>(selectedIndex), topIndex, visibleRows, MENU_ITEMS);
    requestUpdate();
  });
}

void EmailSyncSettingsActivity::handleSelection() {
  if (selectedIndex == 0) {
    const std::string currentUrl = EMAIL_SYNC_STORE.getBaseUrlRaw();
    const std::string prefill = currentUrl.empty() ? "https://" : currentUrl;
    startActivityForResult(
        std::make_unique<KeyboardEntryActivity>(renderer, mappedInput, tr(STR_EMAIL_SYNC_BASE_URL), prefill, 128,
                                                InputType::Url),
        [this](const ActivityResult& result) {
          if (!result.isCancelled) {
            const auto& kb = std::get<KeyboardResult>(result.data);
            const std::string urlToSave = (kb.text == "https://" || kb.text == "http://") ? "" : kb.text;
            EMAIL_SYNC_STORE.setBaseUrl(urlToSave);
            EMAIL_SYNC_STORE.saveToFile();
          }
        });
  } else if (selectedIndex == 1) {
    startActivityForResult(std::make_unique<KeyboardEntryActivity>(renderer, mappedInput, tr(STR_EMAIL_SYNC_TOKEN),
                                                                   EMAIL_SYNC_STORE.getToken(), 128, InputType::Text),
                           [this](const ActivityResult& result) {
                             if (!result.isCancelled) {
                               const auto& kb = std::get<KeyboardResult>(result.data);
                               EMAIL_SYNC_STORE.setToken(kb.text);
                               EMAIL_SYNC_STORE.saveToFile();
                             }
                           });
  } else if (selectedIndex == 2) {
    startActivityForResult(
        std::make_unique<KeyboardEntryActivity>(renderer, mappedInput, tr(STR_EMAIL_SYNC_FOLDER),
                                                EMAIL_SYNC_STORE.getDownloadFolder(), 64, InputType::Text),
        [this](const ActivityResult& result) {
          if (!result.isCancelled) {
            const auto& kb = std::get<KeyboardResult>(result.data);
            EMAIL_SYNC_STORE.setDownloadFolder(kb.text);
            EMAIL_SYNC_STORE.saveToFile();
          }
        });
  } else if (selectedIndex == 3) {
    if (!EMAIL_SYNC_STORE.hasCredentials()) return;
    silentRestartToNetwork(NetworkBootTarget::EMAIL_SYNC);
  }
}

void EmailSyncSettingsActivity::listScreen(UiApp::ScreenType& screen, void* user) {
  static_cast<EmailSyncSettingsActivity*>(user)->buildListScreen(screen);
}

void EmailSyncSettingsActivity::buildListScreen(UiApp::ScreenType& screen) {
  const auto& metrics = UITheme::getInstance().getMetrics();
  screen.setContentMargin(
      fui::Insets{static_cast<int16_t>(metrics.topPadding + TouchHeaderBackButton::height(metrics, mappedInput)), 0,
                  static_cast<int16_t>(metrics.buttonHintsHeight), 0});
  screen.spacer(static_cast<int16_t>(metrics.verticalSpacing));

  std::vector<std::string> values(MENU_ITEMS);
  values[0] = EMAIL_SYNC_STORE.getBaseUrlRaw().empty() ? tr(STR_NOT_SET) : EMAIL_SYNC_STORE.getBaseUrlRaw();
  values[1] = EMAIL_SYNC_STORE.getToken().empty() ? tr(STR_NOT_SET) : "******";
  values[2] = EMAIL_SYNC_STORE.getDownloadFolder();
  values[3] = EMAIL_SYNC_STORE.hasCredentials() ? "" : std::string("[") + tr(STR_SET_CREDENTIALS_FIRST) + "]";

  std::vector<fui::ListItem> items;
  items.reserve(MENU_ITEMS);
  for (int i = 0; i < MENU_ITEMS; i++) {
    fui::ListItem item;
    item.label = I18N.get(menuNames[i]);
    if (!values[i].empty()) item.value = values[i].c_str();
    item.actionValue = static_cast<int16_t>(i);
    items.push_back(item);
  }

  fui::ListProps props;
  props.items = items.data();
  props.count = static_cast<uint16_t>(items.size());
  props.selectedIndex = static_cast<int16_t>(selectedIndex);
  props.action = ACTION_ROW;
  props.inputMask = fui::InputTouch;
  props.valueInset = 8;
  const auto rows = configureUiList(props, screen.theme(), screen.body());
  visibleRows = rows > 0 ? rows : 1;
  topIndex = scrollListBy(topIndex, 0, visibleRows, MENU_ITEMS);
  props.topIndex = static_cast<uint16_t>(topIndex);
  screen.list(props);
}

void EmailSyncSettingsActivity::render(RenderLock&&) {
  renderer.clearScreen();

  const Rect header = TouchHeaderBackButton::headerRect(renderer, mappedInput);
  if (mappedInput.hasTouchHardware()) {
    TouchHeaderBackButton::draw(renderer, uiTarget, header, tr(STR_EMAIL_SYNC), false);
  } else {
    GUI.drawHeader(renderer, header, tr(STR_EMAIL_SYNC));
  }

  uiReady = false;
  app.render();
  uiReady = true;

  const auto labels =
      mappedInput.mapLabels(mappedInput.withBackArrow(tr(STR_BACK)), tr(STR_SELECT), tr(STR_DIR_UP), tr(STR_DIR_DOWN));
  GUI.drawButtonHints(renderer, labels.btn1, labels.btn2, labels.btn3, labels.btn4);
  renderer.displayBuffer();
}
