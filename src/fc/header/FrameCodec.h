#pragma once
#include <cstdint>
#include <string>

namespace fc {

bool recv_exact(int fd, void* buf, size_t nbytes);
bool read_frame(int fd, std::string& out_json, uint32_t max_len);

} // namespace fc
