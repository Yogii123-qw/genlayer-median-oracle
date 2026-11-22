# v0.2.16
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

from genlayer import *

import json
import re
import typing
from datetime import datetime, timezone


# Price is stored scaled by 10**8, so 100.25 USD is 10025000000.
SCALE = 100000000
SCALE_DECIMALS = 8

# A source is an outlier when it sits more than 2 percent from the first median.
OUTLIER_BPS = 200

# A validator accepts the leader median when its own median is within 0.5 percent.
TOLERANCE_BPS = 50

# At least this many sources must be healthy and must agree, or fetch fails.
MIN_HEALTHY = 2

MAX_SOURCES = 10
MAX_URL_LEN = 500
MAX_PATH_LEN = 200
MAX_WEIGHT = 1000
MAX_ASSET_LEN = 32
MAX_BODY_LEN = 200000

# Error classes, following the write-contract skill. Only [EXPECTED] errors
# are compared by message in the validator; anything else means disagree.
ERROR_EXPECTED = "[EXPECTED]"

# Keys checked first when a JSON body is scanned for a price, in priority order.
_PRICE_KEYS = ("price", "rate_float", "last", "close", "usd", "amount")


def _now_unix() -> int:
    # GenVM pins the clock to the transaction datetime, so every validator
    # sees the same value. See the GenLayer Transaction Context docs.
    return int(datetime.now(timezone.utc).timestamp())


def _parse_decimal(text):
    # Parse a decimal string into an integer scaled by SCALE, using only
    # integer arithmetic. Returns None when the text is not a plain number.
    # Accepts an optional sign, an optional fraction, and an optional
    # exponent, for example "100.25", "-3.5", ".5", "1e2". The result is
    # rounded half up on the last kept digit, matching the old scaling.
    if text is None:
        return None
    s = text.strip()
    if len(s) == 0:
        return None
    neg = False
    if s[0] == "-":
        neg = True
        s = s[1:]
    elif s[0] == "+":
        s = s[1:]
    if len(s) == 0:
        return None
    exp = 0
    if "e" in s or "E" in s:
        parts = s.replace("E", "e").split("e")
        if len(parts) != 2:
            return None
        s = parts[0]
        try:
            exp = int(parts[1])
        except ValueError:
            return None
        if exp > 30 or exp < -30:
            return None
    if "." in s:
        whole, frac = s.split(".", 1)
    else:
        whole, frac = s, ""
    if len(whole) == 0 and len(frac) == 0:
        return None
    if len(whole) + len(frac) > 18:
        return None
    if len(whole) > 0 and not whole.isdigit():
        return None
    if len(frac) > 0 and not frac.isdigit():
        return None
    if len(whole) == 0:
        whole = "0"
    try:
        digits = int(whole + frac)
    except ValueError:
        return None
    shift = SCALE_DECIMALS + exp - len(frac)
    if shift >= 0:
        scaled = digits * (10 ** shift)
    else:
        divisor = 10 ** (-shift)
        scaled = (digits + divisor // 2) // divisor
    if neg:
        scaled = -scaled
    if scaled <= 0 or scaled >= 1000000000 * SCALE:
        return None
    return scaled


def _as_number(value):
    # Return the price as a scaled integer, or None when the value is not
    # a usable number. JSON numbers arrive here as int, or as str when
    # they carry a fraction, because the parser keeps fractions as text.
    # Anything else, including a boolean, is rejected.
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        if value <= 0 or value >= 1000000000:
            return None
        return value * SCALE
    if isinstance(value, str):
        return _parse_decimal(value.strip().replace(",", ""))
    return None


def _walk_numbers(node, key, out):
    number = _as_number(node)
    if number is not None:
        out.append((key, number))
        return
    if isinstance(node, dict):
        for child_key, child in node.items():
            _walk_numbers(child, child_key, out)
    elif isinstance(node, list):
        for child in node:
            _walk_numbers(child, key, out)


def _extract_price(body, json_path):
    if body is None:
        return None
    try:
        text = body.decode("utf-8")
    except Exception:
        return None
    if len(text) > MAX_BODY_LEN:
        return None
    data = None
    try:
        # parse_float=str keeps every JSON fraction as text, so no binary
        # fraction ever enters the consensus path. _as_number parses that
        # text with integer arithmetic only.
        data = json.loads(text, parse_float=str)
    except Exception:
        data = None
    if data is not None and json_path:
        node = data
        for part in json_path.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
                node = node[int(part)]
            else:
                return None
        return _as_number(node)
    if data is not None:
        found = []
        _walk_numbers(data, "", found)
        if not found:
            return None
        for want in _PRICE_KEYS:
            for key, number in found:
                if key == want:
                    return number
        return found[0][1]
    match = re.search(r"-?\d[\d,]*(\.\d+)?", text)
    if match is None:
        return None
    return _parse_decimal(match.group(0).replace(",", ""))


def _fetch_one(url, json_path):
    # One dead or misbehaving source must never fail the whole fetch.
    # Anything wrong here means the source is skipped, not that fetch reverts.
    try:
        res = gl.nondet.web.get(url)
        if res.status != 200:
            return None
        return _extract_price(res.body, json_path)
    except Exception:
        return None


def _median_int(values):
    ordered = sorted(values)
    count = len(ordered)
    mid = count // 2
    if count % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) // 2


def _drop_outliers(pairs, median0):
    kept = []
    for price, weight in pairs:
        diff = price - median0
        if diff < 0:
            diff = -diff
        if diff * 10000 <= median0 * OUTLIER_BPS:
            kept.append((price, weight))
    return kept


def _weighted_median(pairs):
    total = 0
    for _, weight in pairs:
        total += weight
    acc = 0
    for price, weight in sorted(pairs):
        acc += weight
        if acc * 2 >= total:
            return price
    return sorted(pairs)[-1][0]


def _handle_leader_error(leaders_res, leader_fn) -> bool:
    leader_msg = leaders_res.message if hasattr(leaders_res, "message") else ""
    try:
        leader_fn()
        return False
    except gl.vm.UserError as e:
        validator_msg = e.message if hasattr(e, "message") else str(e)
        if validator_msg.startswith(ERROR_EXPECTED) and leader_msg.startswith(
            ERROR_EXPECTED
        ):
            return validator_msg == leader_msg
        return False
    except Exception:
        return False


class MedianOracle(gl.Contract):
    owner: Address
    asset: str
    source_count: u256
    source_url: TreeMap[u256, str]
    source_weight: TreeMap[u256, u256]
    source_path: TreeMap[u256, str]
    source_active: TreeMap[u256, bool]
    last_price: u256
    last_updated: u256
    last_sources: u256
    has_price: bool

    def __init__(self, asset: str):
        if len(asset) == 0 or len(asset) > MAX_ASSET_LEN:
            raise gl.vm.UserError("Asset must be 1 to 32 characters")
        self.owner = gl.message.sender_address
        self.asset = asset
        self.source_count = u256(0)
        self.last_price = u256(0)
        self.last_updated = u256(0)
        self.last_sources = u256(0)
        self.has_price = False

    def _require_owner(self) -> None:
        if gl.message.sender_address.as_hex != self.owner.as_hex:
            raise gl.vm.UserError("Only the owner can call this")

    @gl.public.write
    def add_source(self, url: str, weight: u256) -> u256:
        self._require_owner()
        if int(self.source_count) >= MAX_SOURCES:
            raise gl.vm.UserError("Source limit reached")
        if len(url) == 0 or len(url) > MAX_URL_LEN:
            raise gl.vm.UserError("URL must be 1 to 500 characters")
        if not (url.startswith("https://") or url.startswith("http://")):
            raise gl.vm.UserError("URL must start with http:// or https://")
        value = int(weight)
        if value < 1 or value > MAX_WEIGHT:
            raise gl.vm.UserError("Weight must be 1 to 1000")
        for i in range(int(self.source_count)):
            index = u256(i)
            if self.source_active[index] and self.source_url[index] == url:
                raise gl.vm.UserError("Source URL already registered")
        index = self.source_count
        self.source_url[index] = url
        self.source_weight[index] = u256(value)
        self.source_path[index] = ""
        self.source_active[index] = True
        self.source_count = index + u256(1)
        print("source added: ", index)
        return index

    @gl.public.write
    def set_source_path(self, index: u256, json_path: str) -> None:
        self._require_owner()
        if index >= self.source_count:
            raise gl.vm.UserError("Source does not exist")
        if len(json_path) > MAX_PATH_LEN:
            raise gl.vm.UserError("JSON path must be at most 200 characters")
        self.source_path[index] = json_path
        print("source path set: ", index)

    @gl.public.write
    def remove_source(self, index: u256) -> None:
        self._require_owner()
        if index >= self.source_count:
            raise gl.vm.UserError("Source does not exist")
        self.source_active[index] = False
        print("source removed: ", index)

    @gl.public.write
    def fetch_price(self) -> dict[str, typing.Any]:
        if int(self.source_count) == 0:
            raise gl.vm.UserError("No price sources registered")
        # Copy the source list into plain values before the nondet block.
        # Nondet code must not touch storage.
        sources = []
        for i in range(int(self.source_count)):
            index = u256(i)
            if self.source_active[index]:
                sources.append(
                    (
                        self.source_url[index],
                        int(self.source_weight[index]),
                        self.source_path[index],
                    )
                )
        if len(sources) < MIN_HEALTHY:
            raise gl.vm.UserError("Need at least 2 active price sources")

        def leader_fn():
            found = []
            for url, weight, json_path in sources:
                price = _fetch_one(url, json_path)
                if price is not None:
                    found.append((price, weight))
            if len(found) < MIN_HEALTHY:
                raise gl.vm.UserError(
                    ERROR_EXPECTED + " Fewer than 2 healthy price sources"
                )
            median0 = _median_int([price for price, _ in found])
            kept = _drop_outliers(found, median0)
            if len(kept) < MIN_HEALTHY:
                raise gl.vm.UserError(
                    ERROR_EXPECTED + " Fewer than 2 sources agree within 2 percent"
                )
            return {
                "price": _weighted_median(kept),
                "sources_used": len(kept),
                "sources_healthy": len(found),
            }

        def validator_fn(leaders_res) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                return _handle_leader_error(leaders_res, leader_fn)
            leader_data = leaders_res.calldata
            leader_price = leader_data["price"]
            if leader_price <= 0 or leader_data["sources_used"] < MIN_HEALTHY:
                return False
            mine = leader_fn()
            if mine["sources_used"] < MIN_HEALTHY:
                return False
            diff = leader_price - mine["price"]
            if diff < 0:
                diff = -diff
            return diff * 10000 <= leader_price * TOLERANCE_BPS

        result = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)
        now = _now_unix()
        self.last_price = u256(result["price"])
        self.last_updated = u256(now)
        self.last_sources = u256(result["sources_used"])
        self.has_price = True
        print("price fetched: ", result["price"], " from ", result["sources_used"], " sources")
        return {
            "price": result["price"],
            "sources_used": result["sources_used"],
            "sources_healthy": result["sources_healthy"],
            "updated_at": now,
        }

    @gl.public.view
    def get_price(self) -> dict[str, typing.Any]:
        age = 0
        if self.has_price:
            age = _now_unix() - int(self.last_updated)
            if age < 0:
                age = 0
        return {
            "asset": self.asset,
            "price": int(self.last_price),
            "decimals": SCALE_DECIMALS,
            "has_price": self.has_price,
            "updated_at": int(self.last_updated),
            "age_seconds": age,
            "sources_used": int(self.last_sources),
        }

    @gl.public.view
    def is_fresh(self, max_age_seconds: u256) -> bool:
        if not self.has_price:
            return False
        age = _now_unix() - int(self.last_updated)
        return age >= 0 and age <= int(max_age_seconds)

    @gl.public.view
    def get_sources(self) -> dict[str, typing.Any]:
        items = []
        for i in range(int(self.source_count)):
            index = u256(i)
            items.append(
                {
                    "index": i,
                    "url": self.source_url[index],
                    "weight": int(self.source_weight[index]),
                    "json_path": self.source_path[index],
                    "active": self.source_active[index],
                }
            )
        return {"asset": self.asset, "sources": items}

    @gl.public.view
    def get_owner(self) -> dict[str, typing.Any]:
        return {"owner": self.owner.as_hex, "asset": self.asset}
