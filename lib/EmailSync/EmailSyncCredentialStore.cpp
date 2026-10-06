#include "EmailSyncCredentialStore.h"

#include <Logging.h>
#include <ObfuscationUtils.h>

void EmailSyncCredentialStore::toJson(JsonDocument& doc) const {
  doc["baseUrl"] = baseUrl;
  doc["token_obf"] = obfuscation::obfuscateToBase64(token);
  doc["downloadFolder"] = downloadFolder;
}

bool EmailSyncCredentialStore::fromJson(JsonVariantConst doc) {
  bool needsResave = false;

  setBaseUrl(doc["baseUrl"] | "");

  obfuscation::DecodeStatus status = obfuscation::DecodeStatus::INVALID;
  std::string decoded = obfuscation::deobfuscateFromBase64(doc["token_obf"] | "", &status);
  if (status == obfuscation::DecodeStatus::LEGACY && !decoded.empty()) {
    needsResave = true;
  }
  if (status == obfuscation::DecodeStatus::INVALID || status == obfuscation::DecodeStatus::EMPTY || decoded.empty()) {
    decoded = doc["token"] | "";
    if (!decoded.empty()) needsResave = true;
  }
  if (status == obfuscation::DecodeStatus::INVALID && decoded.empty()) {
    LOG_ERR("EmailSync", "Ignoring unreadable email sync token");
  }
  setToken(decoded);

  const char* folder = doc["downloadFolder"] | "/Books/Email";
  setDownloadFolder(folder && folder[0] != '\0' ? folder : "/Books/Email");

  if (needsResave) {
    LOG_DBG("EmailSync", "Resaving credentials to update format");
    requestResave();
  }
  return true;
}

void EmailSyncCredentialStore::setBaseUrl(const std::string& url) {
  ensureLoaded();
  baseUrl = url;
}

std::string EmailSyncCredentialStore::getBaseUrl() const {
  ensureLoaded();
  std::string url = baseUrl;
  if (url.empty()) return url;
  if (url.find("://") == std::string::npos) {
    // Prefer HTTPS: public bridges sit behind Force-SSL; HTTP would 301 and the
    // client historically did not follow redirects (surfaced as a network failure).
    url = "https://" + url;
  }
  while (!url.empty() && url.back() == '/') {
    url.pop_back();
  }
  return url;
}

void EmailSyncCredentialStore::setToken(const std::string& value) {
  ensureLoaded();
  token = value;
}

void EmailSyncCredentialStore::setDownloadFolder(const std::string& folder) {
  ensureLoaded();
  downloadFolder = folder.empty() ? "/Books/Email" : folder;
}

bool EmailSyncCredentialStore::hasCredentials() const {
  ensureLoaded();
  return !baseUrl.empty() && !token.empty();
}
