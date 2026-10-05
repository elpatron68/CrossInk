#include "EmailSyncClient.h"

#include <ArduinoJson.h>
#ifdef SIMULATOR
#include <ArduinoJsonStringCompat.h>
#endif
#include <I18n.h>
#include <Logging.h>
#include <SecureHttpClient.h>
#ifdef SIMULATOR
#include <WiFi.h>
#include <WiFiClientSecure.h>
#endif

#include <cstdio>
#include <cstring>
#include <memory>
#include <string>

#include "EmailSyncCredentialStore.h"
#include "Memory.h"

int EmailSyncClient::lastHttpCode = 0;
int EmailSyncClient::lastTransportError = 0;

namespace {
constexpr uint32_t MIN_FREE_HEAP_FOR_TLS = 35000;
constexpr uint32_t MIN_MAX_ALLOC_HEAP_FOR_TLS = 20000;
constexpr size_t MAX_PENDING_RESPONSE_BYTES = 16384;
constexpr size_t MAX_ACK_RESPONSE_BYTES = 1024;

bool isHttpsUrl(const std::string& url) { return url.rfind("https://", 0) == 0; }

bool insufficientHeap() {
  const uint32_t freeHeap = ESP.getFreeHeap();
  const uint32_t maxAllocHeap = ESP.getMaxAllocHeap();
  if (freeHeap < MIN_FREE_HEAP_FOR_TLS || maxAllocHeap < MIN_MAX_ALLOC_HEAP_FOR_TLS) {
    LOG_ERR("EmailSync", "Insufficient heap for TLS: %u free (need %u), %u maxAlloc (need %u)", freeHeap,
            MIN_FREE_HEAP_FOR_TLS, maxAllocHeap, MIN_MAX_ALLOC_HEAP_FOR_TLS);
    return true;
  }
  return false;
}

void applyBearerHeader(freeink::SecureHttpClient& http) {
  http.addHeader("Accept", "application/json");
  http.addHeader("Authorization", std::string("Bearer ") + EMAIL_SYNC_STORE.getToken());
}

#ifdef SIMULATOR
void applyBearerHeader(HTTPClient& http) {
  http.addHeader("Accept", "application/json");
  http.addHeader("Authorization", (std::string("Bearer ") + EMAIL_SYNC_STORE.getToken()).c_str());
}
#endif

EmailSyncClient::Error mapHttpStatus(const int httpCode) {
  if (httpCode == 401 || httpCode == 403) return EmailSyncClient::AUTH_FAILED;
  if (httpCode == 404) return EmailSyncClient::NOT_FOUND;
  if (httpCode < 0) return EmailSyncClient::NETWORK_ERROR;
  if (httpCode >= 200 && httpCode < 300) return EmailSyncClient::OK;
  return EmailSyncClient::SERVER_ERROR;
}

EmailSyncClient::Error parsePendingJson(const char* body, std::vector<EmailSyncPendingItem>& outItems) {
  JsonDocument doc;
  const DeserializationError error = deserializeJson(doc, body ? body : "");
  if (error) {
    LOG_ERR("EmailSync", "pending JSON parse failed: %s", error.c_str());
    return EmailSyncClient::JSON_ERROR;
  }
  if (!doc.is<JsonArray>()) {
    LOG_ERR("EmailSync", "pending response was not a JSON array");
    return EmailSyncClient::JSON_ERROR;
  }

  outItems.clear();
  JsonArray arr = doc.as<JsonArray>();
  outItems.reserve(arr.size());
  for (JsonObject obj : arr) {
    EmailSyncPendingItem item;
    item.id = obj["id"] | "";
    item.filename = obj["filename"] | "";
    item.bytes = obj["bytes"] | (size_t)0;
    item.sha256 = obj["sha256"] | "";
    item.contentType = obj["content_type"] | "";
    item.receivedAt = obj["received_at"] | "";
    if (item.id.empty() || item.filename.empty()) {
      LOG_ERR("EmailSync", "Skipping pending item with missing id/filename");
      continue;
    }
    outItems.push_back(std::move(item));
  }
  return EmailSyncClient::OK;
}

EmailSyncClient::Error requestJson(const std::string& url, const char* method, char* responseBuf,
                                   size_t responseCap, size_t& responseSize) {
  responseSize = 0;
  if (responseBuf && responseCap > 0) responseBuf[0] = '\0';

  if (isHttpsUrl(url) && insufficientHeap()) return EmailSyncClient::LOW_MEMORY;

#ifdef SIMULATOR
  HTTPClient http;
  std::unique_ptr<WiFiClientSecure> secureClient;
  WiFiClient plainClient;
  if (isHttpsUrl(url)) {
    secureClient.reset(new WiFiClientSecure);
    secureClient->setInsecure();
    http.begin(*secureClient, url.c_str());
  } else {
    http.begin(plainClient, url.c_str());
  }
  applyBearerHeader(http);

  int httpCode = -1;
  if (std::strcmp(method, "POST") == 0) {
    http.addHeader("Content-Length", "0");
    httpCode = http.POST("");
  } else {
    httpCode = http.GET();
  }
  EmailSyncClient::lastHttpCode = httpCode;
  EmailSyncClient::lastTransportError = (httpCode < 0) ? httpCode : 0;

  if (httpCode > 0 && responseBuf && responseCap > 0) {
    const String body = http.getString();
    if (static_cast<size_t>(body.length()) >= responseCap) {
      http.end();
      LOG_ERR("EmailSync", "Response exceeded %u bytes", static_cast<unsigned>(responseCap));
      return EmailSyncClient::SERVER_ERROR;
    }
    std::memcpy(responseBuf, body.c_str(), body.length());
    responseSize = body.length();
    responseBuf[responseSize] = '\0';
  }
  http.end();
  return mapHttpStatus(httpCode);
#else
  freeink::SecureHttpClient http;
  http.setInsecure();
  if (!http.begin(url)) {
    LOG_ERR("EmailSync", "Bad URL: %s", url.c_str());
    return EmailSyncClient::NETWORK_ERROR;
  }
  applyBearerHeader(http);

  bool tooLarge = false;
  bool oom = false;
  const auto onData = [&](const uint8_t* data, size_t len) {
    if (!responseBuf || responseCap == 0) return true;
    if (len > responseCap - 1 - responseSize) {
      tooLarge = true;
      return false;
    }
    std::memcpy(responseBuf + responseSize, data, len);
    responseSize += len;
    responseBuf[responseSize] = '\0';
    return true;
  };

  int httpCode = -1;
  if (std::strcmp(method, "POST") == 0) {
    httpCode = http.sendRequest("POST", nullptr, 0, onData);
  } else {
    httpCode = http.GET(onData);
  }
  EmailSyncClient::lastHttpCode = httpCode;
  EmailSyncClient::lastTransportError = (httpCode < 0) ? httpCode : 0;

  if (tooLarge) {
    http.end();
    LOG_ERR("EmailSync", "Response exceeded %u bytes", static_cast<unsigned>(responseCap));
    return EmailSyncClient::SERVER_ERROR;
  }
  if (oom) {
    http.end();
    return EmailSyncClient::LOW_MEMORY;
  }
  if (httpCode <= 0) {
    http.end();
    return EmailSyncClient::NETWORK_ERROR;
  }
  http.end();
  return mapHttpStatus(httpCode);
#endif
}
}  // namespace

EmailSyncClient::Error EmailSyncClient::fetchPending(std::vector<EmailSyncPendingItem>& outItems) {
  lastHttpCode = 0;
  lastTransportError = 0;
  outItems.clear();

  if (!EMAIL_SYNC_STORE.hasCredentials()) return NO_CREDENTIALS;

  const std::string url = EMAIL_SYNC_STORE.getBaseUrl() + "/v1/pending";
  LOG_DBG("EmailSync", "GET pending: %s (heap=%u)", url.c_str(), (unsigned)ESP.getFreeHeap());

  auto responseBody = makeUniqueNoThrow<char[]>(MAX_PENDING_RESPONSE_BYTES + 1);
  if (!responseBody) return LOW_MEMORY;

  size_t responseSize = 0;
  const Error status =
      requestJson(url, "GET", responseBody.get(), MAX_PENDING_RESPONSE_BYTES + 1, responseSize);
  if (status != OK) return status;
  return parsePendingJson(responseBody.get(), outItems);
}

EmailSyncClient::Error EmailSyncClient::ack(const std::string& itemId) {
  lastHttpCode = 0;
  lastTransportError = 0;
  if (!EMAIL_SYNC_STORE.hasCredentials()) return NO_CREDENTIALS;
  if (itemId.empty()) return NOT_FOUND;

  const std::string url = EMAIL_SYNC_STORE.getBaseUrl() + "/v1/items/" + itemId + "/ack";
  LOG_DBG("EmailSync", "POST ack: %s", url.c_str());

  char responseBody[MAX_ACK_RESPONSE_BYTES + 1];
  size_t responseSize = 0;
  return requestJson(url, "POST", responseBody, sizeof(responseBody), responseSize);
}

std::string EmailSyncClient::contentUrl(const std::string& itemId) {
  return EMAIL_SYNC_STORE.getBaseUrl() + "/v1/items/" + itemId + "/content";
}

std::string EmailSyncClient::errorString(Error error) {
  switch (error) {
    case OK:
      return "";
    case NO_CREDENTIALS:
      return tr(STR_EMAIL_SYNC_NO_CREDENTIALS);
    case NETWORK_ERROR:
      return tr(STR_EMAIL_SYNC_NETWORK_ERROR);
    case AUTH_FAILED:
      return tr(STR_EMAIL_SYNC_AUTH_FAILED);
    case SERVER_ERROR:
      return tr(STR_EMAIL_SYNC_SERVER_ERROR);
    case JSON_ERROR:
      return tr(STR_EMAIL_SYNC_BAD_RESPONSE);
    case NOT_FOUND:
      return tr(STR_EMAIL_SYNC_NOT_FOUND);
    case LOW_MEMORY:
      return tr(STR_EMAIL_SYNC_LOW_MEMORY);
  }
  return tr(STR_EMAIL_SYNC_SERVER_ERROR);
}
