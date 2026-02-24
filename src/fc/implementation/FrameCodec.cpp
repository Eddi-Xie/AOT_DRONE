#include "FrameCodec.h"
#include <arpa/inet.h>
#include <cstring>
#include <sys/socket.h>
#include <unistd.h>

namespace fc {

bool recv_exact(int fd, void* buf, size_t nbytes) {
    uint8_t* p = static_cast<uint8_t*>(buf);
    size_t got = 0;
    while (got < nbytes) {
        ssize_t r = ::recv(fd, p + got, nbytes - got, 0);
        if (r <= 0)
            return false;
        got += static_cast<size_t>(r);
    }
    return true;
}

bool read_frame(int fd, std::string& out_json, uint32_t max_len) {
    uint32_t be_len = 0;
    if (!recv_exact(fd, &be_len, sizeof(be_len)))
        return false;
    uint32_t len = ntohl(be_len);
    if (len == 0 || len > max_len)
        return false;

    out_json.resize(len);
    if (!recv_exact(fd, out_json.data(), len))
        return false;
    return true;
}

} // namespace fc
