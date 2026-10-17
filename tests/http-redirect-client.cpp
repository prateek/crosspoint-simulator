#include <ResumableFetch.h>
#include <iostream>

int main(int argc, char** argv) {
  if (argc != 5) return 2;
  freeink::FetchOptions options;
  options.redirectToHttp = std::string(argv[2]) == "1";
  options.startOffset = std::stoul(argv[3]);
  const int abortAt = std::stoi(argv[4]);
  int polls = 0;
  std::string body, origins;
  freeink::FetchSink sink;
  sink.write = [&](const uint8_t* data, size_t size) {
    body.append(reinterpret_cast<const char*>(data), size);
    return true;
  };
  sink.rewind = [&]() { body.clear(); return true; };
  auto result = freeink::fetchResumable(argv[1], options,
      [&](freeink::SecureHttpClient& http, bool sameOrigin) {
        origins += sameOrigin ? "1" : "0";
        if (sameOrigin) {
          http.setBasicAuth("fixture", "password");
          http.addHeader("X-Fixture-Secret", "private");
        }
      }, sink, [&]() { return ++polls == abortAt; });
  std::cout << result.status << ' ' << result.complete << ' ' << result.aborted
            << ' ' << result.bytes << '\n' << origins << '\n' << body;
}
