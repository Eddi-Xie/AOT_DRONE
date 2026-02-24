#include "CommandReceiver.h"

#include <cctype>
#include <cerrno>
#include <cmath>
#include <cstdlib>
#include <limits>
#include <string>

namespace {

bool skip_ws(const std::string& s, size_t& i) {
    while (i < s.size() && std::isspace(static_cast<unsigned char>(s[i]))) {
        ++i;
    }
    return i < s.size();
}

bool find_key(const std::string& json, const char* key, size_t& value_pos) {
    const std::string token = std::string("\"") + key + "\"";
    const size_t key_pos = json.find(token);
    if (key_pos == std::string::npos) {
        return false;
    }

    size_t i = key_pos + token.size();
    if (!skip_ws(json, i) || json[i] != ':') {
        return false;
    }
    ++i;
    if (!skip_ws(json, i)) {
        return false;
    }
    value_pos = i;
    return true;
}

bool parse_json_string_field(const std::string& json, const char* key, std::string& out_value) {
    size_t i = 0;
    if (!find_key(json, key, i) || json[i] != '"') {
        return false;
    }

    ++i; // consume opening quote
    std::string value;
    while (i < json.size()) {
        const char c = json[i];
        if (c == '"') {
            out_value = value;
            return true;
        }
        if (c == '\\') {
            // Keep parser minimal: reject escaped strings for required fields.
            return false;
        }
        value.push_back(c);
        ++i;
    }
    return false;
}

bool parse_json_number_token(const std::string& json, const char* key, std::string& out_token) {
    size_t i = 0;
    if (!find_key(json, key, i)) {
        return false;
    }

    const size_t start = i;

    if (i < json.size() && (json[i] == '+' || json[i] == '-')) {
        ++i;
    }

    bool has_digit = false;
    while (i < json.size() && std::isdigit(static_cast<unsigned char>(json[i]))) {
        has_digit = true;
        ++i;
    }

    if (i < json.size() && json[i] == '.') {
        ++i;
        while (i < json.size() && std::isdigit(static_cast<unsigned char>(json[i]))) {
            has_digit = true;
            ++i;
        }
    }

    if (!has_digit) {
        return false;
    }

    if (i < json.size() && (json[i] == 'e' || json[i] == 'E')) {
        ++i;
        if (i < json.size() && (json[i] == '+' || json[i] == '-')) {
            ++i;
        }
        bool exp_digit = false;
        while (i < json.size() && std::isdigit(static_cast<unsigned char>(json[i]))) {
            exp_digit = true;
            ++i;
        }
        if (!exp_digit) {
            return false;
        }
    }

    if (i < json.size()) {
        const char end = json[i];
        if (!(end == ',' || end == '}' || end == ']' ||
              std::isspace(static_cast<unsigned char>(end)))) {
            return false;
        }
    }

    out_token = json.substr(start, i - start);
    return true;
}

bool parse_double(const std::string& token, double& out_value) {
    errno = 0;
    char* end = nullptr;
    const double v = std::strtod(token.c_str(), &end);
    if (errno != 0 || end == token.c_str() || *end != '\0') {
        return false;
    }
    if (!std::isfinite(v)) {
        return false;
    }
    out_value = v;
    return true;
}

bool parse_int_like(const std::string& token, int& out_value) {
    double v = 0.0;
    if (!parse_double(token, v)) {
        return false;
    }
    if (v < static_cast<double>(std::numeric_limits<int>::min()) ||
        v > static_cast<double>(std::numeric_limits<int>::max())) {
        return false;
    }

    const double nearest = std::nearbyint(v);
    if (std::fabs(v - nearest) > 1e-6) {
        return false;
    }

    out_value = static_cast<int>(nearest);
    return true;
}

} // namespace

namespace fc {

bool parse_cmd_frame(const std::string& json, CommandFrame& out) {
    std::string type;
    if (!parse_json_string_field(json, "type", type) || type != "CMD") {
        return false;
    }

    std::string seq_token;
    std::string ts_token;
    std::string mode_token;
    if (!parse_json_number_token(json, "seq", seq_token) ||
        !parse_json_number_token(json, "timestamp_s", ts_token) ||
        !parse_json_number_token(json, "desired_mode", mode_token)) {
        return false;
    }

    CommandFrame parsed;
    if (!parse_int_like(seq_token, parsed.seq) || !parse_double(ts_token, parsed.timestamp_s) ||
        !parse_int_like(mode_token, parsed.desired_mode)) {
        return false;
    }
    parsed.raw_json = json;
    out = parsed;
    return true;
}

} // namespace fc
