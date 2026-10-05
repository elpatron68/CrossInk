#pragma once

#include <string>
#include <vector>

#include "EmailSyncClient.h"
#include "activities/Activity.h"
#include "activities/ScreenTransitionRefresh.h"

/**
 * One-tap mail-bridge sync: WiFi → pending list → download → ack.
 * No background polling; WiFi is torn down via silent reboot on exit.
 */
class EmailSyncActivity final : public Activity {
 public:
  static constexpr const char* NAME = "EmailSync";

  explicit EmailSyncActivity(GfxRenderer& renderer, MappedInputManager& mappedInput, bool networkBootReady = false);

  void onEnter() override;
  void onExit() override;
  void loop() override;
  void render(RenderLock&&) override;
  bool preventAutoSleep() override {
    return state == CONNECTING || state == SYNCING || state == DOWNLOADING;
  }

 private:
  enum State {
    NO_CREDENTIALS,
    WIFI_SELECTION,
    CONNECTING,
    SYNCING,
    DOWNLOADING,
    SUCCESS,
    FAILED,
  };

  State state = WIFI_SELECTION;
  ScreenTransitionRefresh screenTransitionRefresh;
  std::string statusMessage;
  std::string detailMessage;
  bool networkBootReady = false;
  bool wifiActivated = false;
  bool cancelDownload = false;
  size_t downloadProgress = 0;
  size_t downloadTotal = 0;
  size_t completedCount = 0;
  size_t totalCount = 0;
  std::vector<EmailSyncPendingItem> pendingItems;

  void onWifiSelectionComplete(bool success);
  void performSync();
  void exitFlow();
};
