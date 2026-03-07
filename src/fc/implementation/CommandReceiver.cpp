#include "CommandReceiver.h"

#include <cctype>
#include <cerrno>
#include <cmath>
#include <cstdlib>
#include <limits>
#include <string>

namespace {

bool skip_ws_range(const std::string& s, size_t& i, size_t end_exclusive) {
    while (i < end_exclusive && std::isspace(static_cast<unsigned char>(s[i]))) {
        ++i;
    }
    return i < end_exclusive;
}

bool is_value_delim(char c) {
    return c == ',' || c == '}' || c == ']' || std::isspace(static_cast<unsigned char>(c));
}

bool parse_json_string_token(const std::string& json, size_t start, size_t end_exclusive,
                             std::string& out_value, size_t& out_next) {
    if (start >= end_exclusive || json[start] != '"') {
        return false;
    }
    size_t i = start + 1;
    std::string value;
    bool escaped = false;
    while (i < end_exclusive) {
        const char c = json[i];
        if (escaped) {
            value.push_back(c);
            escaped = false;
            ++i;
            continue;
        }
        if (c == '\\') {
            escaped = true;
            ++i;
            continue;
        }
        if (c == '"') {
            out_value = value;
            out_next = i + 1;
            return true;
        }
        value.push_back(c);
        ++i;
    }
    return false;
}

bool find_matching_close(const std::string& json, size_t open_pos, size_t end_exclusive,
                         char open_ch, char close_ch, size_t& out_close_pos) {
    if (open_pos >= end_exclusive || json[open_pos] != open_ch) {
        return false;
    }

    int depth = 0;
    bool in_string = false;
    bool escaped = false;
    for (size_t i = open_pos; i < end_exclusive; ++i) {
        const char c = json[i];
        if (in_string) {
            if (escaped) {
                escaped = false;
            } else if (c == '\\') {
                escaped = true;
            } else if (c == '"') {
                in_string = false;
            }
            continue;
        }

        if (c == '"') {
            in_string = true;
            continue;
        }
        if (c == open_ch) {
            ++depth;
            continue;
        }
        if (c == close_ch) {
            --depth;
            if (depth == 0) {
                out_close_pos = i;
                return true;
            }
            if (depth < 0) {
                return false;
            }
            continue;
        }
    }
    return false;
}

bool parse_json_number_token_from(const std::string& json, size_t start, size_t end_exclusive,
                                  std::string& out_token, size_t& out_next) {
    if (start >= end_exclusive) {
        return false;
    }

    size_t i = start;
    if (json[i] == '+' || json[i] == '-') {
        ++i;
    }

    bool has_digit = false;
    while (i < end_exclusive && std::isdigit(static_cast<unsigned char>(json[i]))) {
        has_digit = true;
        ++i;
    }

    if (i < end_exclusive && json[i] == '.') {
        ++i;
        while (i < end_exclusive && std::isdigit(static_cast<unsigned char>(json[i]))) {
            has_digit = true;
            ++i;
        }
    }

    if (!has_digit) {
        return false;
    }

    if (i < end_exclusive && (json[i] == 'e' || json[i] == 'E')) {
        ++i;
        if (i < end_exclusive && (json[i] == '+' || json[i] == '-')) {
            ++i;
        }
        bool exp_digit = false;
        while (i < end_exclusive && std::isdigit(static_cast<unsigned char>(json[i]))) {
            exp_digit = true;
            ++i;
        }
        if (!exp_digit) {
            return false;
        }
    }

    if (i < end_exclusive && !is_value_delim(json[i])) {
        return false;
    }

    out_token = json.substr(start, i - start);
    out_next = i;
    return true;
}

bool skip_json_value(const std::string& json, size_t value_pos, size_t end_exclusive,
                     size_t& out_next_pos) {
    if (value_pos >= end_exclusive) {
        return false;
    }

    const char c = json[value_pos];
    if (c == '"') {
        std::string ignored;
        return parse_json_string_token(json, value_pos, end_exclusive, ignored, out_next_pos);
    }
    if (c == '{') {
        size_t close_pos = 0;
        if (!find_matching_close(json, value_pos, end_exclusive, '{', '}', close_pos)) {
            return false;
        }
        out_next_pos = close_pos + 1;
        return true;
    }
    if (c == '[') {
        size_t close_pos = 0;
        if (!find_matching_close(json, value_pos, end_exclusive, '[', ']', close_pos)) {
            return false;
        }
        out_next_pos = close_pos + 1;
        return true;
    }

    if ((value_pos + 4) <= end_exclusive && json.compare(value_pos, 4, "true") == 0) {
        const size_t end_pos = value_pos + 4;
        if (end_pos == end_exclusive || is_value_delim(json[end_pos])) {
            out_next_pos = end_pos;
            return true;
        }
        return false;
    }
    if ((value_pos + 5) <= end_exclusive && json.compare(value_pos, 5, "false") == 0) {
        const size_t end_pos = value_pos + 5;
        if (end_pos == end_exclusive || is_value_delim(json[end_pos])) {
            out_next_pos = end_pos;
            return true;
        }
        return false;
    }
    if ((value_pos + 4) <= end_exclusive && json.compare(value_pos, 4, "null") == 0) {
        const size_t end_pos = value_pos + 4;
        if (end_pos == end_exclusive || is_value_delim(json[end_pos])) {
            out_next_pos = end_pos;
            return true;
        }
        return false;
    }

    std::string ignored;
    return parse_json_number_token_from(json, value_pos, end_exclusive, ignored, out_next_pos);
}

bool find_root_object_span(const std::string& json, size_t& out_start, size_t& out_end) {
    size_t i = 0;
    skip_ws_range(json, i, json.size());
    if (i >= json.size() || json[i] != '{') {
        return false;
    }

    size_t close_pos = 0;
    if (!find_matching_close(json, i, json.size(), '{', '}', close_pos)) {
        return false;
    }

    size_t tail = close_pos + 1;
    skip_ws_range(json, tail, json.size());
    if (tail != json.size()) {
        return false;
    }

    out_start = i;
    out_end = close_pos;
    return true;
}

bool find_key_in_object(const std::string& json, size_t obj_start, size_t obj_end, const char* key,
                        size_t& value_pos) {
    if (obj_start >= obj_end || json[obj_start] != '{' || json[obj_end] != '}') {
        return false;
    }

    size_t i = obj_start + 1;
    while (i < obj_end) {
        if (!skip_ws_range(json, i, obj_end)) {
            break;
        }
        if (json[i] == ',') {
            ++i;
            continue;
        }
        if (json[i] != '"') {
            return false;
        }

        std::string parsed_key;
        size_t after_key = 0;
        if (!parse_json_string_token(json, i, obj_end, parsed_key, after_key)) {
            return false;
        }
        i = after_key;
        if (!skip_ws_range(json, i, obj_end) || json[i] != ':') {
            return false;
        }
        ++i;
        if (!skip_ws_range(json, i, obj_end)) {
            return false;
        }

        if (parsed_key == key) {
            value_pos = i;
            return true;
        }

        size_t next_pos = 0;
        if (!skip_json_value(json, i, obj_end, next_pos)) {
            return false;
        }
        i = next_pos;
        if (i < obj_end && json[i] == ',') {
            ++i;
        }
    }
    return false;
}

bool find_object_span_in_object(const std::string& json, size_t obj_start, size_t obj_end,
                                const char* key, size_t& out_start, size_t& out_end) {
    size_t value_pos = 0;
    if (!find_key_in_object(json, obj_start, obj_end, key, value_pos)) {
        return false;
    }

    if (value_pos >= obj_end || json[value_pos] != '{') {
        return false;
    }

    size_t close_pos = 0;
    if (!find_matching_close(json, value_pos, obj_end, '{', '}', close_pos)) {
        return false;
    }
    out_start = value_pos;
    out_end = close_pos;
    return true;
}

bool parse_json_string_field_in_object(const std::string& json, size_t obj_start, size_t obj_end,
                                       const char* key, std::string& out_value) {
    size_t value_pos = 0;
    if (!find_key_in_object(json, obj_start, obj_end, key, value_pos)) {
        return false;
    }
    size_t next_pos = 0;
    return parse_json_string_token(json, value_pos, obj_end, out_value, next_pos);
}

bool parse_json_number_token_in_object(const std::string& json, size_t obj_start, size_t obj_end,
                                       const char* key, std::string& out_token) {
    size_t value_pos = 0;
    if (!find_key_in_object(json, obj_start, obj_end, key, value_pos)) {
        return false;
    }
    size_t next_pos = 0;
    return parse_json_number_token_from(json, value_pos, obj_end, out_token, next_pos);
}

bool parse_json_bool_field_in_object(const std::string& json, size_t obj_start, size_t obj_end,
                                     const char* key, bool& out_value) {
    size_t value_pos = 0;
    if (!find_key_in_object(json, obj_start, obj_end, key, value_pos)) {
        return false;
    }

    if ((value_pos + 4) <= obj_end && json.compare(value_pos, 4, "true") == 0) {
        const size_t end_pos = value_pos + 4;
        if (end_pos == obj_end || is_value_delim(json[end_pos])) {
            out_value = true;
            return true;
        }
    }

    if ((value_pos + 5) <= obj_end && json.compare(value_pos, 5, "false") == 0) {
        const size_t end_pos = value_pos + 5;
        if (end_pos == obj_end || is_value_delim(json[end_pos])) {
            out_value = false;
            return true;
        }
    }

    return false;
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

bool parse_optional_number_in_object(const std::string& json, size_t obj_start, size_t obj_end,
                                     const char* key, bool& out_has, double& out_value) {
    std::string token;
    if (!parse_json_number_token_in_object(json, obj_start, obj_end, key, token)) {
        out_has = false;
        return true;
    }

    double parsed_value = 0.0;
    if (!parse_double(token, parsed_value)) {
        return false;
    }

    out_has = true;
    out_value = parsed_value;
    return true;
}

bool parse_optional_bool_in_object(const std::string& json, size_t obj_start, size_t obj_end,
                                   const char* key, bool& out_has, bool& out_value) {
    bool parsed = false;
    if (!parse_json_bool_field_in_object(json, obj_start, obj_end, key, parsed)) {
        out_has = false;
        return true;
    }

    out_has = true;
    out_value = parsed;
    return true;
}

} // namespace

namespace fc {

bool parse_cmd_frame(const std::string& json, CommandFrame& out) {
    size_t root_start = 0;
    size_t root_end = 0;
    if (!find_root_object_span(json, root_start, root_end)) {
        return false;
    }

    std::string type;
    if (!parse_json_string_field_in_object(json, root_start, root_end, "type", type) ||
        type != "CMD") {
        return false;
    }

    std::string seq_token;
    std::string ts_token;
    std::string mode_token;
    if (!parse_json_number_token_in_object(json, root_start, root_end, "seq", seq_token) ||
        !parse_json_number_token_in_object(json, root_start, root_end, "timestamp_s", ts_token) ||
        !parse_json_number_token_in_object(json, root_start, root_end, "desired_mode",
                                           mode_token)) {
        return false;
    }

    CommandFrame parsed;
    if (!parse_int_like(seq_token, parsed.seq) || !parse_double(ts_token, parsed.timestamp_s) ||
        !parse_int_like(mode_token, parsed.desired_mode)) {
        return false;
    }

    if (!parse_optional_bool_in_object(json, root_start, root_end, "arm", parsed.has_arm,
                                       parsed.arm)) {
        return false;
    }

    size_t setpoints_value_pos = 0;
    if (find_key_in_object(json, root_start, root_end, "setpoints", setpoints_value_pos)) {
        if (setpoints_value_pos >= root_end || json[setpoints_value_pos] != '{') {
            return false;
        }
        size_t setpoints_start = 0;
        size_t setpoints_end = 0;
        if (!find_object_span_in_object(json, root_start, root_end, "setpoints", setpoints_start,
                                        setpoints_end)) {
            return false;
        }

        if (!parse_optional_number_in_object(json, setpoints_start, setpoints_end, "roll",
                                             parsed.setpoints.has_roll, parsed.setpoints.roll) ||
            !parse_optional_number_in_object(json, setpoints_start, setpoints_end, "pitch",
                                             parsed.setpoints.has_pitch, parsed.setpoints.pitch) ||
            !parse_optional_number_in_object(json, setpoints_start, setpoints_end, "yaw_rate",
                                             parsed.setpoints.has_yaw_rate,
                                             parsed.setpoints.yaw_rate) ||
            !parse_optional_number_in_object(json, setpoints_start, setpoints_end, "throttle",
                                             parsed.setpoints.has_throttle,
                                             parsed.setpoints.throttle)) {
            return false;
        }
    }

    size_t tracking_value_pos = 0;
    if (find_key_in_object(json, root_start, root_end, "tracking", tracking_value_pos)) {
        if (tracking_value_pos >= root_end || json[tracking_value_pos] != '{') {
            return false;
        }
        size_t tracking_start = 0;
        size_t tracking_end = 0;
        if (!find_object_span_in_object(json, root_start, root_end, "tracking", tracking_start,
                                        tracking_end)) {
            return false;
        }

        std::string tracking_state_token;
        std::string loc_x_token;
        std::string loc_y_token;
        std::string bound_w_token;
        std::string bound_h_token;
        std::string confidence_token;
        std::string vis_seq_token;
        std::string vis_timestamp_token;
        if (!parse_json_number_token_in_object(json, tracking_start, tracking_end, "tracking_state",
                                               tracking_state_token) ||
            !parse_json_number_token_in_object(json, tracking_start, tracking_end, "loc_x",
                                               loc_x_token) ||
            !parse_json_number_token_in_object(json, tracking_start, tracking_end, "loc_y",
                                               loc_y_token) ||
            !parse_json_number_token_in_object(json, tracking_start, tracking_end, "bound_w",
                                               bound_w_token) ||
            !parse_json_number_token_in_object(json, tracking_start, tracking_end, "bound_h",
                                               bound_h_token) ||
            !parse_json_number_token_in_object(json, tracking_start, tracking_end, "confidence",
                                               confidence_token) ||
            !parse_json_number_token_in_object(json, tracking_start, tracking_end, "vis_seq",
                                               vis_seq_token) ||
            !parse_json_number_token_in_object(json, tracking_start, tracking_end,
                                               "vis_timestamp_s", vis_timestamp_token)) {
            return false;
        }

        if (!parse_int_like(tracking_state_token, parsed.tracking.tracking_state) ||
            !parse_double(loc_x_token, parsed.tracking.loc_x) ||
            !parse_double(loc_y_token, parsed.tracking.loc_y) ||
            !parse_double(bound_w_token, parsed.tracking.bound_w) ||
            !parse_double(bound_h_token, parsed.tracking.bound_h) ||
            !parse_double(confidence_token, parsed.tracking.confidence) ||
            !parse_int_like(vis_seq_token, parsed.tracking.vis_seq) ||
            !parse_double(vis_timestamp_token, parsed.tracking.vis_timestamp_s)) {
            return false;
        }

        if (parsed.tracking.tracking_state < 1 || parsed.tracking.tracking_state > 4) {
            return false;
        }

        parsed.tracking.has_tracking = true;
    }

    parsed.raw_json = json;
    out = parsed;
    return true;
}

} // namespace fc
