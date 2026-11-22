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

The live deployment registers two sources, each with a pinned JSON path.

CoinGecko: `https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd`, path `bitcoin.usd`

Coinbase: `https://api.coinbase.com/v2/prices/BTC-USD/spot`, path `data.amount`

Binance was tried and dropped: the Studio network could not reach it from the deployment region. CoinDesk parses with the built-in auto-detect and is covered by the local tests; it is not registered on the live deployment.

Any other asset works the same way as long as each source answers with a JSON body that holds one clear price number. When a source changes its format, `set_source_path` pins the new path without a redeploy.

## Files

contracts/median_oracle.py holds the contract. tests/test_median_oracle.py holds the local tests, 20 cases that cover the full flow, the validator band, and every revert path.

## Test it locally

The tests use genlayer-test in Direct Mode with mocked web responses. Install genlayer-test, then run pytest from the repo root:

`pytest tests/test_median_oracle.py -q`

The shipped contract targets the old-style SDK pin directly, and local tests deploy that exact file with no header swap, so the tested file is byte-identical to the file Studio receives. `gl.vm.run_nondet_unsafe` and `gl.nondet.web.get` are both present in the local bundle, so the custom validator path runs locally too: the leader runs through mocked web calls, and the captured validator is re-run with changed mocks to prove it accepts a move inside the 0.5 percent band and rejects a move outside it.

## Verification & Test Results

Local tests run in genlayer-test Direct Mode with mocked web responses. The suite has 20 cases and all 20 pass. Coverage: source registration and owner-only guards, fetch with three agreeing sources, weighted median, one outlier dropped, one dead source dropped, bad format dropped, unparseable body dropped, revert when fewer than two healthy sources or fewer than two sources agree, the validator band (accepts a 0.2 percent move, rejects a 2 percent move, rejects a lying leader), freshness with a warped clock, source removal, JSON path pinning, price parsing across CoinGecko, Binance, and CoinDesk shapes, and pinned JSON paths for the three live source shapes (CoinGecko bitcoin.usd, Coinbase data.amount, Kraken result.XXBTZUSD.c.0).

Price parsing uses integer arithmetic only. No binary fraction enters the consensus path: JSON fractions are kept as text and parsed into integers scaled by 10 to the power of 8.

## Live Deployment Proof (GenLayer Studio)

Network: GenLayer StudioNet (chain id 61999)
Contract Address: 0x91C4a6197905e03966c63B82f072f1706f8D8188
Deployment Transaction Hash: 0xc283b33ced6d2a00da4bd2734b891688f9046a44a5694c09031cb10119b535b0
Consensus Execution Proof: fetch_price, transaction 0xa037c7494b9efab88e538a62c9bd8641f7a5f9e5cd14c1839c121b5fa69c3785, FINALIZED, reached consensus, no USER_ERROR

Registered sources: CoinGecko, transaction 0x1deeae98faa68b8336ee7e8cc577e9739aa836ea923c1aa2cc54a2c057810f59, with JSON path "bitcoin.usd" set by transaction 0x239342c6dd0cda5d6ccfee325fc0db4f277a8ae1c8da1c8050eeb864c86807d6; Coinbase, transaction 0x7c383d3cecbadda4c1faf9bab17d1852940951ebe39656b86a4bbdba53dc26bd, with JSON path "data.amount" set by transaction 0x86c50aba448897d117b90528487bb29796b19415640355fc33fb18bf1f6f018f.

Verified live read, 22 November 2026: get_price returns price 6067700000000 at scale 10 to the power of 8, which is 60,677.00 USD, with has_price true and sources_used 2.

Contract on the Explorer: https://explorer-studio.genlayer.com/address/0x91C4a6197905e03966c63B82f072f1706f8D8188
Deploy transaction on the Explorer: https://explorer-studio.genlayer.com/tx/0xc283b33ced6d2a00da4bd2734b891688f9046a44a5694c09031cb10119b535b0
Fetch transaction on the Explorer: https://explorer-studio.genlayer.com/tx/0xa037c7494b9efab88e538a62c9bd8641f7a5f9e5cd14c1839c121b5fa69c3785

## Deploy it on Studio

Open Studio, create a new Intelligent Contract, paste contracts/median_oracle.py, and deploy with the asset label as the constructor argument, for example `BTC/USD`. The deployer becomes the owner. Then call `add_source` for each price URL, call `fetch_price`, and read the result with `get_price`. The contract is deployed; the live deployment proof above records the address and the FINALIZED fetch transaction. This repo does not submit anything on its own.

## Limits, stated plainly

The Studio deploy is live and one fetch reached consensus there with two sources, so the validator path is proven on live Studio for that run. The local tests still prove the logic with mocked web responses, not with real ones. The price is only as good as its sources: if two sources are wrong in the same direction, the median follows them. Free price APIs can change format or start rate limiting without notice; a source that breaks is dropped, and the fetch fails cleanly when fewer than two healthy sources are left. There is no time-weighted average, so a single fetch still reflects one moment in time. The owner is a single key with no timelock, so whoever holds that key controls the source list.

## License

MIT.
