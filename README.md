# Median Oracle

Median Oracle is an Intelligent Contract for GenLayer. It reads the price of one asset from several public web sources, drops the readings that sit too far from the rest, and stores the median of what is left. It uses no language model. The hard part it solves is consensus over web data that moves: the leader and the validators fetch at slightly different times, so the contract agrees on a price band instead of an exact number.

## How it works

The owner registers price sources with `add_source`. Each source is a URL plus a weight. The contract fetches every active source with `gl.nondet.web.get` and pulls a price out of the response body. Most public price APIs answer with JSON, so the parser reads JSON first. It looks for a price under a known key (`price`, `rate_float`, `last`, `close`, `usd`, `amount`), and it falls back to the first number in the body when the shape is unknown. If a source is pinned with `set_source_path`, the parser follows that dotted JSON path instead, which is the right call when a body holds more than one number.

A source that fails, answers with a non-200 status, or returns a body with no usable number is dropped. It never fails the whole fetch. Fetch needs at least two healthy sources, and it needs at least two sources to agree within 2 percent of the first median. The survivors are combined into a weighted median, and that value is stored with the transaction timestamp and the count of sources used.

Prices are stored scaled by 10 to the power of 8, so 100.25 USD is stored as 10025000000. `get_price` reports the stored price, its age in seconds, and how many sources went into it. `is_fresh` answers whether the stored price is younger than a caller-supplied limit, so another contract can refuse a stale price before it uses one.

## Consensus: the custom validator

Strict equality cannot work here. Two validators that fetch the same price a second apart will read different numbers, and a strict check would fail consensus on every fetch. The contract uses `gl.vm.run_nondet_unsafe` with a custom validator, the pattern the GenLayer docs call the default choice for non-deterministic work.

The validator re-runs the whole fetch itself and accepts the leader median only when its own median lands within 0.5 percent of it. It also checks that the leader median is positive and came from at least two sources. If the leader raised an expected error (too few healthy sources, or too few sources in agreement), the validator agrees only when its own run raises the exact same error. Any other mismatch means disagree, which rotates the leader.

## Functions

`add_source(url, weight)` registers a source. Owner only. At most 10 sources, weight 1 to 1000.

`set_source_path(index, json_path)` pins a dotted JSON path for one source. Owner only. Use it when the auto-detect would pick the wrong number out of a body.

`remove_source(index)` deactivates a source. Owner only. The entry stays in storage so indexes never shift.

`fetch_price()` fetches all active sources, drops outliers, stores the median. Anyone can call it.

`get_price()` returns the asset, the stored price, the decimal scale (8), whether a price exists, the update time, the age in seconds, and the source count.

`is_fresh(max_age_seconds)` returns true only when a price exists and is no older than the limit.

`get_sources()` lists every registered source with its URL, weight, JSON path, and active flag.

`get_owner()` returns the owner address and the asset.

## Suggested sources for BTC/USD

These three public endpoints all parse with the built-in auto-detect, no JSON path needed.

CoinGecko: `https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd`

Binance: `https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT`

CoinDesk: `https://api.coindesk.com/v1/bpi/currentprice.json`

Any other asset works the same way as long as each source answers with a JSON body that holds one clear price number. When a source changes its format, `set_source_path` pins the new path without a redeploy.

## Files

contracts/median_oracle.py holds the contract. tests/test_median_oracle.py holds the local tests, 19 cases that cover the full flow, the validator band, and every revert path.

## Test it locally

The tests use genlayer-test in Direct Mode with mocked web responses. Install genlayer-test, then run pytest from the repo root:

`pytest tests/test_median_oracle.py -q`

The test file swaps only the contract header to a locally available SDK pin. The shipped contract itself targets Studio-next, and that header stays untouched. `gl.vm.run_nondet_unsafe` and `gl.nondet.web.get` are both present in the local bundle, so the custom validator path runs locally too: the leader runs through mocked web calls, and the captured validator is re-run with changed mocks to prove it accepts a move inside the 0.5 percent band and rejects a move outside it.

## Verification & Test Results

Local tests run in genlayer-test Direct Mode with mocked web responses. The suite has 19 cases and all 19 pass. Coverage: source registration and owner-only guards, fetch with three agreeing sources, weighted median, one outlier dropped, one dead source dropped, bad format dropped, unparseable body dropped, revert when fewer than two healthy sources or fewer than two sources agree, the validator band (accepts a 0.2 percent move, rejects a 2 percent move, rejects a lying leader), freshness with a warped clock, source removal, JSON path pinning, and price parsing across CoinGecko, Binance, and CoinDesk shapes.

Price parsing uses integer arithmetic only. No binary fraction enters the consensus path: JSON fractions are kept as text and parsed into integers scaled by 10 to the power of 8.

## Live Deployment Proof (GenLayer Studio)

Network: TBD (GenLayer Studio)
Contract Address: TBD
Deployment Transaction Hash: TBD
Consensus Execution Proof: TBD (FINALIZED transaction hash)

No live deployment exists yet. These slots are filled in after the Studio deploy and a FINALIZED write transaction are verified on the Explorer.

## Deploy it on Studio

Open Studio, create a new Intelligent Contract, paste contracts/median_oracle.py, and deploy with the asset label as the constructor argument, for example `BTC/USD`. The deployer becomes the owner. Then call `add_source` for each price URL, call `fetch_price`, and read the result with `get_price`. Deploy is not done yet; it needs a browser session, and this repo does not submit anything on its own.

## Limits, stated plainly

The Studio deploy has not been run yet, so the validator path on live Studio still needs that check. The local tests prove the logic with mocked web responses, not with real ones. The price is only as good as its sources: if two sources are wrong in the same direction, the median follows them. Free price APIs can change format or start rate limiting without notice; a source that breaks is dropped, and the fetch fails cleanly when fewer than two healthy sources are left. There is no time-weighted average, so a single fetch still reflects one moment in time. The owner is a single key with no timelock, so whoever holds that key controls the source list.

## License

MIT.
