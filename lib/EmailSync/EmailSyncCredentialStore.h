#pragma once
#include <ArduinoJson.h>
#include <PersistableStore.h>

#include <string>

/**
 * SD-backed credentials for the CrossInk mail-bridge sync client.
 * Token is XOR-obfuscated with the device MAC (same pattern as KOReader).
 */
class EmailSyncCredentialStore : public PersistableStore<EmailSyncCredentialStore> {
 private:
  std::string baseUrl;
  std::string token;
  std::string downloadFolder = "/Books/Email";

  EmailSyncCredentialStore() = default;
  ~EmailSyncCredentialStore() = default;

  friend class PersistableStore<EmailSyncCredentialStore>;

 public:
  static const char* getFilePath() { return "/.crosspoint/email_sync.json"; }
  void toJson(JsonDocument& doc) const;
  bool fromJson(JsonVariantConst doc);

  void setBaseUrl(const std::string& url);
  const std::string& getBaseUrlRaw() const {
    ensureLoaded();
    return baseUrl;
  }
  // Normalized URL with protocol and without trailing slash.
  // Bare hosts default to https:// (mail bridge is typically behind Force-SSL).
  std::string getBaseUrl() const;

  void setToken(const std::string& value);
  const std::string& getToken() const {
    ensureLoaded();
    return token;
  }

  void setDownloadFolder(const std::string& folder);
  const std::string& getDownloadFolder() const {
    ensureLoaded();
    return downloadFolder;
  }

  bool hasCredentials() const;
};

#define EMAIL_SYNC_STORE EmailSyncCredentialStore::getInstance()
