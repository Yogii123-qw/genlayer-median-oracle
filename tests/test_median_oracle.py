"""Local Direct Mode tests for MedianOracle.

NOTE ON THE SDK PIN: the shipped contract (contracts/median_oracle.py) targets the old-style pin directly (# v0.2.16 header, py-genlayer pin 1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6), the same pin the Studio build of 22 Nov 2026 proved deployable. Local tests deploy the shipped file verbatim, with no header swap and no API replacements, so the file tested here is byte-identical to the file Studio receives. The Studio deploy remains the authoritative check.

Web calls are mocked with direct_vm.mock_web. The custom validator is
exercised through direct_vm.run_validator, which re-runs the captured
validator function against the current mocks, so the leader and validator
can be made to see different prices.
"""

from pathlib import Path

SHIPPED_PIN = "1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6"

SCALE = 100000000

URL_A = "https://price-a.example.com/btc"
URL_B = "https://price-b.example.com/btc"
URL_C = "https://price-c.example.com/btc"


def _local_copy(tmp_path):
    # The shipped contract already targets the old-style pin, so tests
    # deploy it verbatim: no header swap, no API replacements. If the
    # header ever drifts back to a newer pin, this guard fails loudly
    # instead of silently testing a different file than Studio receives.
    src = Path("contracts/median_oracle.py").read_text()
    assert src.startswith("# v0.2.16\n"), "shipped header drifted from v0.2.16"
    assert SHIPPED_PIN in src, "shipped header drifted from pin 1jb45aa8"
    target = tmp_path / "median_oracle_local.py"
    target.write_text(src)
    return str(target)
def _hex(addr):
    return "0x" + bytes(addr).hex()


def _deploy(direct_vm, direct_deploy, tmp_path, deployer=None):
    # The deployer becomes the contract owner, so deploy as the test owner.
    if deployer is not None:
        direct_vm.sender = deployer
    return direct_deploy(_local_copy(tmp_path), "BTC/USD")


def _mock_price(direct_vm, url, price):
    direct_vm.mock_web(url, {"status": 200, "body": '{"price": %s}' % price})


def _mock_all(direct_vm, pa="100.00", pb="100.00", pc="100.00"):
    _mock_price(direct_vm, URL_A, pa)
    _mock_price(direct_vm, URL_B, pb)
    _mock_price(direct_vm, URL_C, pc)


def test_add_source_stores_fields(direct_vm, direct_deploy, direct_alice, tmp_path):
    contract = _deploy(direct_vm, direct_deploy, tmp_path, direct_alice)
    direct_vm.sender = direct_alice
    assert contract.add_source(URL_A, 3) == 0
    assert contract.add_source(URL_B, 1) == 1
    sources = contract.get_sources()
    assert sources["asset"] == "BTC/USD"
    assert len(sources["sources"]) == 2
    assert sources["sources"][0]["url"] == URL_A
    assert sources["sources"][0]["weight"] == 3
    assert sources["sources"][0]["active"] is True
    assert contract.get_owner()["owner"].lower() == _hex(direct_alice).lower()


def test_add_source_rejects_bad_input(direct_vm, direct_deploy, direct_alice, direct_bob, tmp_path):
    contract = _deploy(direct_vm, direct_deploy, tmp_path, direct_alice)
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("Only the owner"):
        contract.add_source(URL_A, 1)
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("http:// or https://"):
        contract.add_source("ftp://price.example.com/btc", 1)
    with direct_vm.expect_revert("Weight must be 1 to 1000"):
        contract.add_source(URL_A, 0)
    with direct_vm.expect_revert("Weight must be 1 to 1000"):
        contract.add_source(URL_A, 1001)
    contract.add_source(URL_A, 1)
    with direct_vm.expect_revert("already registered"):
        contract.add_source(URL_A, 1)


def test_get_price_before_fetch(direct_vm, direct_deploy, direct_alice, tmp_path):
    contract = _deploy(direct_vm, direct_deploy, tmp_path, direct_alice)
    direct_vm.sender = direct_alice
    price = contract.get_price()
    assert price["has_price"] is False
    assert price["price"] == 0
    assert price["sources_used"] == 0
    assert contract.is_fresh(60) is False


def test_fetch_three_agree(direct_vm, direct_deploy, direct_alice, tmp_path):
    contract = _deploy(direct_vm, direct_deploy, tmp_path, direct_alice)
    direct_vm.sender = direct_alice
    contract.add_source(URL_A, 1)
    contract.add_source(URL_B, 1)
    contract.add_source(URL_C, 1)
    _mock_all(direct_vm)
    result = contract.fetch_price()
    assert result["price"] == 100 * SCALE
    assert result["sources_used"] == 3
    assert result["sources_healthy"] == 3
    stored = contract.get_price()
    assert stored["has_price"] is True
    assert stored["price"] == 100 * SCALE
    assert stored["sources_used"] == 3
    assert stored["age_seconds"] == 0
    assert contract.is_fresh(60) is True


def test_fetch_weights_move_median(direct_vm, direct_deploy, direct_alice, tmp_path):
    contract = _deploy(direct_vm, direct_deploy, tmp_path, direct_alice)
    direct_vm.sender = direct_alice
    contract.add_source(URL_A, 3)
    contract.add_source(URL_B, 1)
    contract.add_source(URL_C, 1)
    _mock_all(direct_vm, pa="100.00", pb="101.00", pc="102.00")
    result = contract.fetch_price()
    # Plain median is 101.00, but weight 3 on the 100.00 source pulls the
    # weighted median down to 100.00.
    assert result["price"] == 100 * SCALE
    assert result["sources_used"] == 3


def test_fetch_one_outlier_dropped(direct_vm, direct_deploy, direct_alice, tmp_path):
    contract = _deploy(direct_vm, direct_deploy, tmp_path, direct_alice)
    direct_vm.sender = direct_alice
    contract.add_source(URL_A, 1)
    contract.add_source(URL_B, 1)
    contract.add_source(URL_C, 1)
    _mock_all(direct_vm, pa="100.00", pb="100.10", pc="105.00")
    result = contract.fetch_price()
    # 105.00 is 4.9 percent above the first median 100.10, so it is dropped.
    # Weighted median of the two survivors is the lower one, 100.00.
    assert result["price"] == 100 * SCALE
    assert result["sources_used"] == 2
    assert result["sources_healthy"] == 3


def test_fetch_one_source_dead(direct_vm, direct_deploy, direct_alice, tmp_path):
    contract = _deploy(direct_vm, direct_deploy, tmp_path, direct_alice)
    direct_vm.sender = direct_alice
    contract.add_source(URL_A, 1)
    contract.add_source(URL_B, 1)
    contract.add_source(URL_C, 1)
    _mock_price(direct_vm, URL_A, "100.00")
    _mock_price(direct_vm, URL_B, "100.00")
    # URL_C has no mock at all: the fetch raises inside the per-source
    # handler and the source is dropped instead of failing the whole call.
    result = contract.fetch_price()
    assert result["price"] == 100 * SCALE
    assert result["sources_used"] == 2
    assert result["sources_healthy"] == 2


def test_fetch_dead_and_bad_format_sources_dropped(
    direct_vm, direct_deploy, direct_alice, tmp_path
):
    contract = _deploy(direct_vm, direct_deploy, tmp_path, direct_alice)
    direct_vm.sender = direct_alice
    contract.add_source(URL_A, 1)
    contract.add_source(URL_B, 1)
    contract.add_source(URL_C, 1)
    _mock_price(direct_vm, URL_A, "100.00")
    _mock_price(direct_vm, URL_B, "100.00")
    direct_vm.mock_web(URL_C, {"status": 500, "body": "service unavailable"})
    result = contract.fetch_price()
    assert result["price"] == 100 * SCALE
    assert result["sources_used"] == 2


def test_fetch_unparseable_body_dropped(direct_vm, direct_deploy, direct_alice, tmp_path):
    contract = _deploy(direct_vm, direct_deploy, tmp_path, direct_alice)
    direct_vm.sender = direct_alice
    contract.add_source(URL_A, 1)
    contract.add_source(URL_B, 1)
    contract.add_source(URL_C, 1)
    _mock_price(direct_vm, URL_A, "100.00")
    _mock_price(direct_vm, URL_B, "100.00")
    direct_vm.mock_web(URL_C, {"status": 200, "body": "no numbers here"})
    result = contract.fetch_price()
    assert result["price"] == 100 * SCALE
    assert result["sources_used"] == 2


def test_fetch_fewer_than_two_healthy_reverts(
    direct_vm, direct_deploy, direct_alice, tmp_path
):
    contract = _deploy(direct_vm, direct_deploy, tmp_path, direct_alice)
    direct_vm.sender = direct_alice
    contract.add_source(URL_A, 1)
    contract.add_source(URL_B, 1)
    contract.add_source(URL_C, 1)
    _mock_price(direct_vm, URL_A, "100.00")
    with direct_vm.expect_revert("Fewer than 2 healthy"):
        contract.fetch_price()


def test_fetch_no_agreement_reverts(direct_vm, direct_deploy, direct_alice, tmp_path):
    contract = _deploy(direct_vm, direct_deploy, tmp_path, direct_alice)
    direct_vm.sender = direct_alice
    contract.add_source(URL_A, 1)
    contract.add_source(URL_B, 1)
    contract.add_source(URL_C, 1)
    _mock_all(direct_vm, pa="100.00", pb="105.00", pc="110.00")
    with direct_vm.expect_revert("Fewer than 2 sources agree"):
        contract.fetch_price()


def test_fetch_without_sources_reverts(direct_vm, direct_deploy, direct_alice, tmp_path):
    contract = _deploy(direct_vm, direct_deploy, tmp_path, direct_alice)
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("No price sources"):
        contract.fetch_price()


def test_validator_agrees_inside_band(direct_vm, direct_deploy, direct_alice, tmp_path):
    contract = _deploy(direct_vm, direct_deploy, tmp_path, direct_alice)
    direct_vm.sender = direct_alice
    contract.add_source(URL_A, 1)
    contract.add_source(URL_B, 1)
    contract.add_source(URL_C, 1)
    _mock_all(direct_vm)
    contract.fetch_price()
    # Validator re-fetches and sees 100.20, a 0.2 percent move, inside the
    # 0.5 percent band.
    direct_vm.clear_mocks()
    _mock_all(direct_vm, pa="100.20", pb="100.20", pc="100.20")
    assert direct_vm.run_validator() is True


def test_validator_rejects_outside_band(direct_vm, direct_deploy, direct_alice, tmp_path):
    contract = _deploy(direct_vm, direct_deploy, tmp_path, direct_alice)
    direct_vm.sender = direct_alice
    contract.add_source(URL_A, 1)
    contract.add_source(URL_B, 1)
    contract.add_source(URL_C, 1)
    _mock_all(direct_vm)
    contract.fetch_price()
    # Validator re-fetches and sees 102.00, a 2 percent move, outside the
    # 0.5 percent band.
    direct_vm.clear_mocks()
    _mock_all(direct_vm, pa="102.00", pb="102.00", pc="102.00")
    assert direct_vm.run_validator() is False


def test_validator_rejects_lying_leader(direct_vm, direct_deploy, direct_alice, tmp_path):
    contract = _deploy(direct_vm, direct_deploy, tmp_path, direct_alice)
    direct_vm.sender = direct_alice
    contract.add_source(URL_A, 1)
    contract.add_source(URL_B, 1)
    contract.add_source(URL_C, 1)
    _mock_all(direct_vm)
    contract.fetch_price()
    lying = {"price": 200 * SCALE, "sources_used": 3, "sources_healthy": 3}
    assert direct_vm.run_validator(leader_result=lying) is False


def test_is_fresh_with_warp(direct_vm, direct_deploy, direct_alice, tmp_path):
    contract = _deploy(direct_vm, direct_deploy, tmp_path, direct_alice)
    direct_vm.sender = direct_alice
    contract.add_source(URL_A, 1)
    contract.add_source(URL_B, 1)
    contract.add_source(URL_C, 1)
    _mock_all(direct_vm)
    direct_vm.warp("2026-11-22T10:00:00Z")
    contract.fetch_price()
    assert contract.get_price()["age_seconds"] == 0
    direct_vm.warp("2026-11-22T10:00:30Z")
    assert contract.get_price()["age_seconds"] == 30
    assert contract.is_fresh(60) is True
    assert contract.is_fresh(10) is False


def test_remove_source(direct_vm, direct_deploy, direct_alice, direct_bob, tmp_path):
    contract = _deploy(direct_vm, direct_deploy, tmp_path, direct_alice)
    direct_vm.sender = direct_alice
    contract.add_source(URL_A, 1)
    contract.add_source(URL_B, 1)
    contract.add_source(URL_C, 1)
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("Only the owner"):
        contract.remove_source(2)
    direct_vm.sender = direct_alice
    contract.remove_source(2)
    assert contract.get_sources()["sources"][2]["active"] is False
    _mock_all(direct_vm)
    result = contract.fetch_price()
    assert result["sources_used"] == 2
    assert result["price"] == 100 * SCALE


def test_json_path_pins_ambiguous_body(
    direct_vm, direct_deploy, direct_alice, tmp_path
):
    contract = _deploy(direct_vm, direct_deploy, tmp_path, direct_alice)
    direct_vm.sender = direct_alice
    contract.add_source(URL_A, 1)
    contract.add_source(URL_B, 1)
    contract.add_source(URL_C, 1)
    # Source A returns a body where the first "price" key is a decoy.
    # Without a path the auto-detect would take 999.99; the pinned path
    # takes the real 50.00 under ticker.last.
    direct_vm.mock_web(
        URL_A,
        {"status": 200, "body": '{"price": 999.99, "ticker": {"last": 50.00}}'},
    )
    _mock_price(direct_vm, URL_B, "50.00")
    _mock_price(direct_vm, URL_C, "50.00")
    contract.set_source_path(0, "ticker.last")
    result = contract.fetch_price()
    assert result["price"] == 50 * SCALE
    assert result["sources_used"] == 3


def test_price_parsing_across_formats(
    direct_vm, direct_deploy, direct_alice, tmp_path
):
    contract = _deploy(direct_vm, direct_deploy, tmp_path, direct_alice)
    direct_vm.sender = direct_alice
    contract.add_source(URL_A, 1)
    contract.add_source(URL_B, 1)
    contract.add_source(URL_C, 1)
    # CoinGecko shape, Binance shape, CoinDesk shape.
    direct_vm.mock_web(
        URL_A, {"status": 200, "body": '{"bitcoin": {"usd": 100.00}}'}
    )
    direct_vm.mock_web(
        URL_B, {"status": 200, "body": '{"symbol": "BTCUSDT", "price": "100.00"}'}
    )
    direct_vm.mock_web(
        URL_C,
        {"status": 200, "body": '{"bpi": {"USD": {"rate_float": 100.00}}}'},
    )
    result = contract.fetch_price()
    assert result["price"] == 100 * SCALE
    assert result["sources_used"] == 3


def test_pinned_paths_live_source_shapes(
    direct_vm, direct_deploy, direct_alice, tmp_path
):
    contract = _deploy(direct_vm, direct_deploy, tmp_path, direct_alice)
    direct_vm.sender = direct_alice
    contract.add_source(URL_A, 1)
    contract.add_source(URL_B, 1)
    contract.add_source(URL_C, 1)
    # Exact bodies served live: CoinGecko nests the price under
    # bitcoin.usd, Coinbase under data.amount as a string, Kraken under
    # result.XXBTZUSD.c[0] as a string. Each source gets its pinned path.
    direct_vm.mock_web(
        URL_A, {"status": 200, "body": '{"bitcoin": {"usd": 60795}}'}
    )
    direct_vm.mock_web(
        URL_B,
        {
            "status": 200,
            "body": '{"data": {"amount": "60702.755", "base": "BTC", "currency": "USD"}}',
        },
    )
    direct_vm.mock_web(
        URL_C,
        {
            "status": 200,
            "body": '{"result": {"XXBTZUSD": {"c": ["60795.0", "1.0"]}}}',
        },
    )
    contract.set_source_path(0, "bitcoin.usd")
    contract.set_source_path(1, "data.amount")
    contract.set_source_path(2, "result.XXBTZUSD.c.0")
    sources = contract.get_sources()["sources"]
    assert sources[0]["json_path"] == "bitcoin.usd"
    assert sources[1]["json_path"] == "data.amount"
    assert sources[2]["json_path"] == "result.XXBTZUSD.c.0"
    result = contract.fetch_price()
    # Prices are 60795, 60702.755, 60795: the median is 60795 and the
    # low source sits 0.15 percent below it, inside the 2 percent band.
    assert result["price"] == 60795 * SCALE
    assert result["sources_used"] == 3
    assert result["sources_healthy"] == 3
