# v0.4.0
# { "Depends": "py-genlayer:kzybg95x1gn21dw72m7hyg0maaap04n4nx55j9yt6fkdqxtz6bz0" }

import json
import re
import typing
from datetime import datetime, timezone

import genlayer as gl
from genlayer.types import *


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


def _scale_price(price):
    if price is None:
        return None
    if price != price:
        return None
    if price in (float("inf"), float("-inf")):
        return None
    if price <= 0 or price >= 1000000000:
        return None
    scaled = int(price * SCALE + 0.5)
    if scaled <= 0:
        return None
    return scaled


def _as_number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
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
        data = json.loads(text)
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
        return _scale_price(_as_number(node))
    if data is not None:
        found = []
        _walk_numbers(data, "", found)
        if not found:
            return None
        for want in _PRICE_KEYS:
            for key, number in found:
                if key == want:
                    return _scale_price(number)
        return _scale_price(found[0][1])
    match = re.search(r"-?\d[\d,]*(\.\d+)?", text)
    if match is None:
        return None
    try:
        return _scale_price(float(match.group(0).replace(",", "")))
    except ValueError:
        return None


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


class MedianOracle(gl.contract.Contract):
    owner: Address
    asset: str
    source_count: u256
    source_url: gl.storage.TreeMap[u256, str]
    source_weight: gl.storage.TreeMap[u256, u256]
    source_path: gl.storage.TreeMap[u256, str]
    source_active: gl.storage.TreeMap[u256, bool]
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
