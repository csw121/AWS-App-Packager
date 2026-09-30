"""Conservative, bounded presentation of untrusted process and project text."""

import re

MASK = "[REDACTED]"
_ASSIGNMENT = re.compile(
    r"(?i)(?<![\w.-])([\"']?(?:[\w.-]*(?:password|passwd|secret|api[_-]?key|access[_-]?key|"
    r"private[_-]?key|token|credential)[\w.-]*)[\"']?\s*[:=]\s*)"
    r"(?:\$\{[^}\r\n]+\}|\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;\}\]]+)"
)
_DOCKER_ENV = re.compile(
    r"(?im)(\bENV[ \t]+[\w.-]*(?:password|passwd|secret|api[_-]?key|access[_-]?key|"
    r"private[_-]?key|token|credential)[\w.-]*[ \t]+)([^\r\n]+)"
)
_XML_SECRET = re.compile(
    r"(?i)(<([\w:.-]*(?:password|passwd|secret|api[_-]?key|access[_-]?key|"
    r"private[_-]?key|token|credential)[\w:.-]*)\b[^>]*>)([^<]*)(</\2\s*>)"
)
_PRIVATE_KEY = re.compile(
    r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----.*?(?:-----END (?:[A-Z ]+ )?PRIVATE KEY-----|\Z)",
    re.DOTALL,
)
_KEY = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b|\b(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,})\b")
_CONNECTION = re.compile(
    r"(?i)\b(?:jdbc:[a-z0-9]+:|(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://)[^\s\"'<>]+"
)
_USER_URL = re.compile(r"(?i)(https?://)[^/\s:@]+:[^/\s@]+@[^\s\"'<>]+")
_PRIVATE_URL = re.compile(
    r"(?i)https?://(?:10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|"
    r"172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+|169\.254\.\d+\.\d+|"
    r"[a-z0-9.-]+\.(?:internal|local))(?::\d+)?(?:/[^\s\"'<>]*)?"
)
_PERSONAL_PATH = re.compile(r"(?i)(?:[A-Z]:[\\/]+Users[\\/]+[^\r\n\"'<>]+|/(?:home|Users)/[^\r\n\"'<>]+)")
_BEARER = re.compile(r"(?i)\b(?:Bearer|Basic)\s+[A-Za-z0-9_./+=-]+")
_QUERY_SECRET = re.compile(
    r"(?i)([?&](?:token|key|password|secret|signature|credential|x-amz-[\w-]+)=)[^&#\s]+"
)


def redact(text: object, max_length: int = 16_384) -> str:
    """No raw logs are persisted; this is defense in depth, not a secret guarantee."""
    if max_length <= 0:
        return ""
    value = str(text)
    # Bound work before regexes; a cut key/assignment is still masked by end-of-input rules.
    value = value[: max(0, max_length) + 4096]
    value = _PRIVATE_KEY.sub(MASK, value)
    value = _DOCKER_ENV.sub(lambda match: match[1] + MASK, value)
    value = _XML_SECRET.sub(lambda match: match[1] + MASK + match[4], value)
    value = _ASSIGNMENT.sub(lambda match: match[1] + MASK, value)
    value = _KEY.sub(MASK, value)
    value = _CONNECTION.sub("[CONNECTION REDACTED]", value)
    value = _USER_URL.sub("[AUTHENTICATED URL REDACTED]", value)
    value = _PRIVATE_URL.sub("[PRIVATE URL REDACTED]", value)
    value = _PERSONAL_PATH.sub("[PERSONAL PATH REDACTED]", value)
    value = _BEARER.sub("[AUTHORIZATION REDACTED]", value)
    value = _QUERY_SECRET.sub(lambda match: match[1] + MASK, value)
    if len(value) > max_length:
        return (value[: max(0, max_length - 14)] + "\n[TRUNCATED]")[:max_length]
    return value


def has_secret_candidate(text: str) -> bool:
    """Only values, never variable names alone, trigger the input blocker."""
    if _PRIVATE_KEY.search(text) or _KEY.search(text) or _USER_URL.search(text):
        return True
    candidates = [match[0][len(match[1]) :] for match in _ASSIGNMENT.finditer(text)]
    candidates.extend(match[2] for match in _DOCKER_ENV.finditer(text))
    candidates.extend(match[3] for match in _XML_SECRET.finditer(text))
    for candidate in candidates:
        value = candidate.strip().strip("\"'")
        if value and not re.fullmatch(
            r"(?i)(?:\$\{[A-Z0-9_:.-]+\}|\$[A-Z0-9_]+|none|null|true|false|"
            r"changeme|replace[_-]?me|example|placeholder|<[^>]+>)",
            value,
        ):
            return True
    return False
