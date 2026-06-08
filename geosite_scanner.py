"""
geosite_scanner.py — парсер protobuf dat-файлов xray-core (geosite_*.dat / geoip_*.dat).

Используется dashboard.py для поиска домена/IP по содержимому GeoFile-баз:
  • lookup_domain(file, domain) -> [категории где есть match]
  • lookup_ip(file, ip)         -> [категории где IP попадает в CIDR]
  • list_categories(file)        -> [(category, entries_count), ...]

Дизайн:
  • Ad-hoc protobuf-декодер (varint + length-delimited) без зависимости от google.protobuf.
  • Структура парсинга 1:1 повторяет geo.rs из MIT-проекта zxc-rv/XKeen-UI.
  • Кэш бинарей dat в локальной папке (по умолчанию backups/dat_cache/).
  • Инвалидация по mtime (mtime роутера передаётся снаружи).
  • Никакой нагрузки на роутер при lookup — всё на ПК после первой перекачки.

Формат dat (protobuf от v2fly):
  GeoSiteList { GeoSite[] }             # tag=1, length-delimited
  GeoSite { country_code: string,       # tag=1, length-delimited (utf-8)
            domain:       Domain[] }    # tag=2, length-delimited (repeated)
  Domain  { type:  enum,                # tag=1, varint (0=Plain 1=Regex 2=Domain 3=Full)
            value: string }             # tag=2, length-delimited

  GeoIPList { GeoIP[] }
  GeoIP   { country_code: string,
            cidr:         CIDR[] }
  CIDR    { ip:     bytes (4 или 16),   # tag=1, length-delimited
            prefix: uint32 }            # tag=2, varint
"""

import os
import re
import ipaddress
from typing import Optional, List, Tuple, Callable, Dict

# --- protobuf wire types ---
WIRE_VARINT = 0
WIRE_FIXED64 = 1
WIRE_LENGTH_DELIMITED = 2
WIRE_FIXED32 = 5

# --- defaults ---
ROUTER_DAT_DIR = "/opt/etc/xray/dat"
DAT_CACHE_DIR_DEFAULT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "backups", "dat_cache"
)

# Domain.type
DOMAIN_TYPE_PLAIN = 0
DOMAIN_TYPE_REGEX = 1
DOMAIN_TYPE_DOMAIN = 2  # suffix
DOMAIN_TYPE_FULL = 3

DOMAIN_TYPE_LABELS = {
    DOMAIN_TYPE_PLAIN: "plain",
    DOMAIN_TYPE_REGEX: "regex",
    DOMAIN_TYPE_DOMAIN: "domain",
    DOMAIN_TYPE_FULL: "full",
}


# ============================================================================
# Low-level protobuf decoder
# ============================================================================

def _decode_varint(buf: bytes, offset: int) -> Tuple[int, int]:
    """Decode varint. (value, new_offset). Raises ValueError on overflow / EOB."""
    result = 0
    shift = 0
    while True:
        if offset >= len(buf):
            raise ValueError("varint past end-of-buffer")
        b = buf[offset]
        offset += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, offset
        shift += 7
        if shift >= 64:
            raise ValueError("varint too long")


def _decode_key(buf: bytes, offset: int) -> Tuple[int, int, int]:
    """Decode protobuf field key. Returns (tag, wire_type, new_offset)."""
    key, offset = _decode_varint(buf, offset)
    return key >> 3, key & 0x7, offset


def _skip_field(buf: bytes, offset: int, wire_type: int) -> int:
    """Skip an unknown field. Returns new offset."""
    if wire_type == WIRE_VARINT:
        _, offset = _decode_varint(buf, offset)
        return offset
    if wire_type == WIRE_FIXED64:
        return offset + 8
    if wire_type == WIRE_LENGTH_DELIMITED:
        length, offset = _decode_varint(buf, offset)
        return offset + length
    if wire_type == WIRE_FIXED32:
        return offset + 4
    raise ValueError(f"unknown wire_type {wire_type}")


def _read_length_delimited(buf: bytes, offset: int) -> Tuple[memoryview, int]:
    """Read length-prefixed slice. Returns (slice as memoryview, new_offset)."""
    length, offset = _decode_varint(buf, offset)
    end = offset + length
    if end > len(buf):
        raise ValueError("length-delimited past end-of-buffer")
    return memoryview(buf)[offset:end], end


# ============================================================================
# Domain matching (Plain / Regex / Domain-suffix / Full)
# ============================================================================

def _match_domain(domain_type: int, value: str, target_lower: str) -> bool:
    """v2fly domain match rules."""
    if not value:
        return False
    if domain_type == DOMAIN_TYPE_PLAIN:
        return value in target_lower
    if domain_type == DOMAIN_TYPE_REGEX:
        try:
            return re.search(value, target_lower) is not None
        except re.error:
            return False
    if domain_type == DOMAIN_TYPE_DOMAIN:
        # suffix match: 'foo.example.com' ends with 'example.com' AND boundary is '.'
        if target_lower == value:
            return True
        return target_lower.endswith("." + value)
    if domain_type == DOMAIN_TYPE_FULL:
        return target_lower == value
    return False


# ============================================================================
# CIDR matching
# ============================================================================

def _match_cidr(cidr_ip_bytes: bytes, prefix: int, target_ip: ipaddress._BaseAddress) -> bool:
    """Bitwise CIDR match. cidr_ip_bytes — raw 4 or 16 bytes."""
    if len(cidr_ip_bytes) == 4 and isinstance(target_ip, ipaddress.IPv4Address):
        n = int.from_bytes(cidr_ip_bytes, "big")
        t = int(target_ip)
        if prefix > 32:
            return False
        if prefix == 0:
            return True
        shift = 32 - prefix
        return (n >> shift) == (t >> shift)
    if len(cidr_ip_bytes) == 16 and isinstance(target_ip, ipaddress.IPv6Address):
        n = int.from_bytes(cidr_ip_bytes, "big")
        t = int(target_ip)
        if prefix > 128:
            return False
        if prefix == 0:
            return True
        shift = 128 - prefix
        return (n >> shift) == (t >> shift)
    return False


# ============================================================================
# Geo file walker
# ============================================================================

def _iter_entries(data: bytes):
    """Итератор по top-level entries файла dat.
    Yields (country_code: str, payload_list: List[memoryview]) для каждого GeoSite/GeoIP.
    """
    offset = 0
    n = len(data)
    while offset < n:
        try:
            tag, wt, offset = _decode_key(data, offset)
        except ValueError:
            return
        if tag != 1 or wt != WIRE_LENGTH_DELIMITED:
            try:
                offset = _skip_field(data, offset, wt)
                continue
            except ValueError:
                return
        try:
            entry_buf, offset = _read_length_delimited(data, offset)
        except ValueError:
            return
        # Parse one entry: country_code (tag=1) + repeated payload (tag=2).
        country_code = ""
        payloads = []
        eoff = 0
        ebuf = bytes(entry_buf)
        while eoff < len(ebuf):
            try:
                t, w, eoff = _decode_key(ebuf, eoff)
            except ValueError:
                break
            if t == 1 and w == WIRE_LENGTH_DELIMITED:
                try:
                    s, eoff = _read_length_delimited(ebuf, eoff)
                    country_code = bytes(s).decode("utf-8", errors="replace")
                except ValueError:
                    break
            elif t == 2 and w == WIRE_LENGTH_DELIMITED:
                try:
                    p, eoff = _read_length_delimited(ebuf, eoff)
                    payloads.append(bytes(p))
                except ValueError:
                    break
            else:
                try:
                    eoff = _skip_field(ebuf, eoff, w)
                except ValueError:
                    break
        yield country_code, payloads


def _parse_domain_payload(payload: bytes) -> Tuple[int, str]:
    """Parse single Domain message → (type, value)."""
    offset = 0
    dt = 0
    val = ""
    while offset < len(payload):
        try:
            t, w, offset = _decode_key(payload, offset)
        except ValueError:
            break
        if t == 1 and w == WIRE_VARINT:
            try:
                v, offset = _decode_varint(payload, offset)
                dt = int(v)
            except ValueError:
                break
        elif t == 2 and w == WIRE_LENGTH_DELIMITED:
            try:
                s, offset = _read_length_delimited(payload, offset)
                val = bytes(s).decode("utf-8", errors="replace")
            except ValueError:
                break
        else:
            try:
                offset = _skip_field(payload, offset, w)
            except ValueError:
                break
    return dt, val


def _parse_cidr_payload(payload: bytes) -> Tuple[bytes, int]:
    """Parse single CIDR message → (ip_bytes, prefix)."""
    offset = 0
    ip_b = b""
    prefix = 0
    while offset < len(payload):
        try:
            t, w, offset = _decode_key(payload, offset)
        except ValueError:
            break
        if t == 1 and w == WIRE_LENGTH_DELIMITED:
            try:
                s, offset = _read_length_delimited(payload, offset)
                ip_b = bytes(s)
            except ValueError:
                break
        elif t == 2 and w == WIRE_VARINT:
            try:
                v, offset = _decode_varint(payload, offset)
                prefix = int(v)
            except ValueError:
                break
        else:
            try:
                offset = _skip_field(payload, offset, w)
            except ValueError:
                break
    return ip_b, prefix


# ============================================================================
# Public API: type detection & lookups
# ============================================================================

def detect_dat_type(local_path: str) -> str:
    """Возвращает 'geosite' / 'geoip' / 'unknown' эвристически по первому entry.

    Логика (1:1 с zxc-rv detect_geo_file_type):
      Первое поле первого payload (под tag=2 entry):
        tag=1 varint            → Domain.type        → geosite
        tag=1 length-delimited  → CIDR.ip (bytes)    → geoip
        tag=2 varint            → CIDR.prefix        → geoip
        tag=2 length-delimited  → Domain.value       → geosite
    """
    try:
        with open(local_path, "rb") as f:
            data = f.read()
    except OSError:
        return "unknown"
    for _country, payloads in _iter_entries(data):
        if not payloads:
            continue
        p = payloads[0]
        offset = 0
        try:
            t, w, _ = _decode_key(p, offset)
        except ValueError:
            return "unknown"
        if t == 1 and w == WIRE_VARINT:
            return "geosite"
        if t == 1 and w == WIRE_LENGTH_DELIMITED:
            return "geoip"
        if t == 2 and w == WIRE_VARINT:
            return "geoip"
        if t == 2 and w == WIRE_LENGTH_DELIMITED:
            return "geosite"
        return "unknown"
    return "unknown"


def lookup_domain(local_path: str, domain: str) -> List[str]:
    """Возвращает отсортированный список категорий где domain matches."""
    if not domain:
        return []
    target = domain.strip().lower().rstrip(".")
    if not target:
        return []
    try:
        with open(local_path, "rb") as f:
            data = f.read()
    except OSError:
        return []
    found = set()
    for country, payloads in _iter_entries(data):
        if not country or country in found:
            continue
        for p in payloads:
            dt, val = _parse_domain_payload(p)
            if not val:
                continue
            if _match_domain(dt, val.lower(), target):
                found.add(country)
                break
    return sorted(found)


def lookup_ip(local_path: str, ip_str: str) -> List[str]:
    """Возвращает отсортированный список категорий где IP попадает в CIDR."""
    try:
        target = ipaddress.ip_address(ip_str)
    except ValueError:
        return []
    try:
        with open(local_path, "rb") as f:
            data = f.read()
    except OSError:
        return []
    found = set()
    for country, payloads in _iter_entries(data):
        if not country or country in found:
            continue
        for p in payloads:
            ip_b, prefix = _parse_cidr_payload(p)
            if not ip_b:
                continue
            if _match_cidr(ip_b, prefix, target):
                found.add(country)
                break
    return sorted(found)


def list_categories(local_path: str) -> List[Tuple[str, int]]:
    """[(country_code, entries_count), ...] для всех категорий в файле."""
    try:
        with open(local_path, "rb") as f:
            data = f.read()
    except OSError:
        return []
    out = []
    for country, payloads in _iter_entries(data):
        if country:
            out.append((country, len(payloads)))
    out.sort(key=lambda x: x[0])
    return out


# ============================================================================
# Cache management
# ============================================================================

FetchCallback = Callable[[str], Optional[bytes]]


class DatCache:
    """Локальный кэш dat-файлов. fetch_callback(remote_path) -> bytes.

    Использование из dashboard.py:
        cache = DatCache(fetch_callback=lambda path: _fetch_dat_via_ssh(path))
        local_path = cache.ensure(filename='geosite_v2fly.dat', remote_mtime=...)
        categories = lookup_domain(local_path, 'claude.ai')
    """

    def __init__(self, cache_dir: str = DAT_CACHE_DIR_DEFAULT,
                 fetch_callback: Optional[FetchCallback] = None):
        self.cache_dir = cache_dir
        self.fetch_callback = fetch_callback
        os.makedirs(self.cache_dir, exist_ok=True)

    def local_path(self, filename: str) -> str:
        return os.path.join(self.cache_dir, filename)

    def is_cached(self, filename: str, remote_mtime: Optional[int] = None) -> bool:
        p = self.local_path(filename)
        if not os.path.exists(p):
            return False
        if remote_mtime is None:
            return True
        try:
            return int(os.path.getmtime(p)) >= int(remote_mtime)
        except OSError:
            return False

    def ensure(self, filename: str, remote_mtime: Optional[int] = None,
               remote_path: Optional[str] = None, force: bool = False) -> Optional[str]:
        """Скачивает с роутера если кэш устарел/отсутствует. Возвращает локальный путь или None."""
        if not force and self.is_cached(filename, remote_mtime):
            return self.local_path(filename)
        if self.fetch_callback is None:
            # без callback можем только использовать существующий локальный кэш
            return self.local_path(filename) if os.path.exists(self.local_path(filename)) else None
        rp = remote_path or f"{ROUTER_DAT_DIR}/{filename}"
        data = self.fetch_callback(rp)
        if not data:
            return None
        local = self.local_path(filename)
        tmp = local + ".tmp"
        try:
            with open(tmp, "wb") as f:
                f.write(data)
            # atomic replace (на Windows os.replace допустим)
            os.replace(tmp, local)
            if remote_mtime:
                try:
                    os.utime(local, (int(remote_mtime), int(remote_mtime)))
                except OSError:
                    pass
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            return None
        return local

    def invalidate(self, filename: Optional[str] = None) -> int:
        """Удалить кэш (одного файла или весь). Возвращает количество удалённых файлов."""
        n = 0
        if filename:
            p = self.local_path(filename)
            if os.path.exists(p):
                try:
                    os.unlink(p)
                    n += 1
                except OSError:
                    pass
            return n
        for f in os.listdir(self.cache_dir):
            p = os.path.join(self.cache_dir, f)
            if os.path.isfile(p):
                try:
                    os.unlink(p)
                    n += 1
                except OSError:
                    pass
        return n

    def cached_files(self) -> List[Dict]:
        """[{name, size, mtime}, ...] для всех файлов в кэше."""
        out = []
        if not os.path.exists(self.cache_dir):
            return out
        for f in sorted(os.listdir(self.cache_dir)):
            p = os.path.join(self.cache_dir, f)
            if os.path.isfile(p) and not f.endswith(".tmp"):
                try:
                    st = os.stat(p)
                    out.append({
                        "name": f,
                        "size": st.st_size,
                        "mtime": int(st.st_mtime),
                    })
                except OSError:
                    pass
        return out


# ============================================================================
# CLI helper (для ручного теста: python geosite_scanner.py PATH domain|ip VALUE)
# ============================================================================

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: python geosite_scanner.py <path-to-dat> <domain|ip> <value>")
        print("       python geosite_scanner.py <path-to-dat> detect")
        print("       python geosite_scanner.py <path-to-dat> list")
        sys.exit(1)
    path = sys.argv[1]
    mode = sys.argv[2]
    if mode == "detect":
        print(detect_dat_type(path))
    elif mode == "list":
        for cat, cnt in list_categories(path):
            print(f"{cat}\t{cnt}")
    elif mode in ("domain", "ip"):
        if len(sys.argv) < 4:
            print(f"Mode '{mode}' requires a value argument")
            sys.exit(1)
        fn = lookup_domain if mode == "domain" else lookup_ip
        for c in fn(path, sys.argv[3]):
            print(c)
    else:
        print(f"Unknown mode: {mode}")
        sys.exit(1)
