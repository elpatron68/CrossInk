#pragma once

#include <cstddef>
#include <string>
#include <vector>

struct EmailSyncPendingItem {
  std::string id;
  std::string filename;
  size_t bytes = 0;
  std::string sha256;
  std::string contentType;
  std::string receivedAt;
};

/**
 * HTTP client for the CrossInk mail-bridge /v1 API.
 * Downloads themselves go through HttpDownloader with a Bearer token.
 */
class EmailSyncClient {
 public:
  enum Error {
    OK = 0,
    NO_CREDENTIALS,
    NETWORK_ERROR,
    AUTH_FAILED,
    SERVER_ERROR,
    JSON_ERROR,
    NOT_FOUND,
    LOW_MEMORY,
  };

  static Error fetchPending(std::vector<EmailSyncPendingItem>& outItems);
  static Error ack(const std::string& itemId);

  // Absolute URL for GET /v1/items/{id}/content
  static std::string contentUrl(const std::string& itemId);

  static std::string errorString(Error error);

  static int lastHttpCode;
  static int lastTransportError;
};
