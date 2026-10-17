#pragma once

#include <SecureHttpClient.h>

#include <cstdint>
#include <cctype>
#include <functional>
#include <string>

namespace freeink {

inline std::string fetchOrigin(const std::string& url) {
  const size_t schemeEnd = url.find("://");
  if (schemeEnd == std::string::npos) return {};
  std::string scheme = url.substr(0, schemeEnd);
  const size_t hostStart = schemeEnd + 3;
  const size_t hostEnd = url.find_first_of("/?#", hostStart);
  std::string authority = url.substr(hostStart, hostEnd == std::string::npos ? std::string::npos : hostEnd - hostStart);
  const size_t at = authority.rfind('@');
  if (at != std::string::npos) authority.erase(0, at + 1);
  for (auto* part : {&scheme, &authority}) {
    for (char& c : *part) c = static_cast<char>(tolower(static_cast<unsigned char>(c)));
  }
  const size_t bracket = authority.rfind(']');  // IPv6 literal: its colons are not a port
  if (authority.find(':', bracket == std::string::npos ? 0 : bracket) == std::string::npos) {
    authority += scheme == "https" ? ":443" : ":80";
  }
  return scheme + "://" + authority;
}


struct FetchOptions {
  size_t startOffset = 0;
  bool redirectToHttp = false;
};

struct FetchSink {
  std::function<bool(const uint8_t*, size_t)> write;
  std::function<bool()> rewind;
  std::function<void(size_t, size_t)> progress;
};

struct FetchResult {
  int status = 0;
  size_t bytes = 0;
  size_t total = 0;
  bool complete = false;
  bool stopped = false;
  bool aborted = false;
};

// The simulator HTTP transport buffers each response, so it cannot replay a
// partial body. Redirects use the same origin policy as the firmware helper.
inline FetchResult fetchResumable(
    const std::string& startUrl, const FetchOptions& options,
    const std::function<void(SecureHttpClient&, bool)>& configure,
    const FetchSink& sink,
    const SecureHttpClient::AbortCallback& shouldAbort = nullptr) {
  FetchResult result;
  result.bytes = options.startOffset;
  std::string url = startUrl;
  const std::string startOrigin = fetchOrigin(startUrl);
  constexpr int maxRedirects = 5;
  for (int redirects = 0; ; ++redirects) {
    if (shouldAbort && shouldAbort()) {
      result.aborted = true;
      return result;
    }
    SecureHttpClient http;
    if (!http.begin(url)) {
      result.status = -1;
      return result;
    }
    if (configure) configure(http, fetchOrigin(url) == startOrigin);
    if (options.startOffset)
      http.addHeader("Range", "bytes=" + std::to_string(options.startOffset) + "-");
    result.status = http.GET();
    if (shouldAbort && shouldAbort()) {
      result.aborted = true;
      return result;
    }
    if (result.status == 301 || result.status == 302 || result.status == 303 ||
        result.status == 307 || result.status == 308) {
      const std::string location = http.getHeader("location");
      if (redirects >= maxRedirects || location.empty() ||
          !SecureHttpClient::resolveUrl(url, location, url))
        return result;
      if (options.redirectToHttp && url.rfind("https://", 0) == 0)
        url.replace(0, 8, "http://");
      continue;
    }
    if (result.status < 200 || result.status >= 300 || !http.responseComplete())
      return result;
    if (options.startOffset && result.status == 200) {
      if (!sink.rewind || !sink.rewind()) {
        result.stopped = true;
        return result;
      }
      result.bytes = 0;
    }
    const String body = http.getString();
    if (!body.s.empty() &&
        (!sink.write || !sink.write(
                            reinterpret_cast<const uint8_t*>(body.s.data()),
                            body.s.size()))) {
      result.stopped = true;
      return result;
    }
    result.bytes += body.s.size();
    result.total = result.bytes;
    if (sink.progress) sink.progress(result.bytes, result.total);
    result.complete = true;
    return result;
  }
}

}  // namespace freeink
