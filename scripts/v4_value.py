"""Aave V4 liquidation census (Ethereum), standalone.

This is the ad-hoc script used for the Aave V4 numbers in docs/FINDINGS.md section 1.3,
published as it was run. It is deliberately NOT part of the mev-scout CLI: V4 has a
different event and oracle surface, and the CLI covers V3 only. There is no test suite
behind this file.

V4 `LiquidationCall` topic0 is derived from `aave/aave-v4`
src/spoke/interfaces/ISpoke.sol @40232a0 (keccak computed locally); the tuple includes
`PremiumDelta (int256,int256,uint256)`. Pricing goes through `Spoke.ORACLE()` (0x38013f02)
-> `getReservePrice(uint256)` (0xd45c35ff), with decimals from word 3 of
`getReserve(uint256)` (0x77778db3). The liquidator's share of collateral is
`removed * sharesToLiquidator / sharesLiquidated`.

Usage:
    ETHERSCAN_API_KEY=... RPC=<archive endpoint> python scripts/v4_value.py

Gas is reported in ETH, not USD: pricing it would need an ETH/USD feed this script does
not carry.
"""
import os, json, time, urllib.request, csv, sys
from decimal import Decimal as D
from collections import Counter, defaultdict
ek=os.environ["ETHERSCAN_API_KEY"]; RPC=os.environ["RPC"]
T="0x2a1f12d996f530f89d8038aa293f9fde81cac44b6dfd6225e3358d09b78a4a37"
def rpc(method, params):
    for i in range(7):
        time.sleep(0.2)
        req=urllib.request.Request(RPC, data=json.dumps({"jsonrpc":"2.0","id":1,"method":method,"params":params}).encode(), headers={"Content-Type":"application/json"})
        try:
            r=json.load(urllib.request.urlopen(req, timeout=30))
        except Exception as e:
            time.sleep(2**i); continue
        if "error" in r:
            if "rate" in str(r["error"]).lower() or r["error"].get("code")==429: time.sleep(2**i); continue
            raise RuntimeError(str(r["error"])[:120])
        return r["result"]
    raise RuntimeError("rpc unavailable")
cache={}
def call(to,data,b):
    k=(to,data,b)
    if k not in cache: cache[k]=rpc("eth_call",[{"to":to,"data":data},hex(b)])
    return cache[k]
w=lambda h,i: int(h[2+64*i:2+64*(i+1)],16)
u=f"https://api.etherscan.io/v2/api?chainid=1&module=logs&action=getLogs&topic0={T}&fromBlock=0&toBlock=latest&page=1&offset=1000&apikey={ek}"
d=json.load(urllib.request.urlopen(u,timeout=60)); rows=d["result"]
if d["status"]!="1": raise SystemExit(f"etherscan refused: {d.get('message')} {str(d.get('result'))[:200]}")
# The 1000-row page cap is a silent-truncation trap: hitting it means the census is
# incomplete, not that there were exactly 1000 liquidations. Refuse rather than under-report.
if len(rows)>=1000: raise SystemExit(f"hit the {len(rows)}-row page cap; paginate before trusting these totals")
out=[]
for r in rows:
    spoke=r["address"]; b=int(r["blockNumber"],16); cid=int(r["topics"][1],16); did=int(r["topics"][2],16)
    data=r["data"]; liq="0x"+data[2+24:2+64]
    debt_amt=w(data,2); coll_removed=w(data,7); sh_liq=w(data,8); sh_to=w(data,9)
    oracle="0x"+call(spoke,"0x38013f02",b)[-40:]
    odec=int(call(oracle,"0x313ce567",b),16)
    def reserve(rid): res=call(spoke,"0x77778db3"+hex(rid)[2:].rjust(64,"0"),b); return "0x"+res[2+24:2+64], w(res,3)
    cu,cdec=reserve(cid); du,ddec=reserve(did)
    pc=int(call(oracle,"0xd45c35ff"+hex(cid)[2:].rjust(64,"0"),b),16); pd=int(call(oracle,"0xd45c35ff"+hex(did)[2:].rjust(64,"0"),b),16)
    if pc==0 or pd==0 or sh_liq==0: out.append(dict(tx=r["transactionHash"],unpriced=1)); continue
    coll_to_liq=D(coll_removed)*D(sh_to)/D(sh_liq)
    coll_usd=coll_to_liq/D(10**cdec)*D(pc)/D(10**odec); debt_usd=D(debt_amt)/D(10**ddec)*D(pd)/D(10**odec)
    gas_eth=D(int(r["gasUsed"],16))*D(int(r["gasPrice"],16))/D(10**18)
    out.append(dict(tx=r["transactionHash"],block=b,ts=int(r["timeStamp"],16),spoke=spoke,liq=liq,coll=cu,debt=du,coll_usd=coll_usd,debt_usd=debt_usd,gross=coll_usd-debt_usd,gas_eth=gas_eth,unpriced=0))
priced=[o for o in out if not o["unpriced"]]
# gas in USD: use ETH price via collateral/debt when WETH involved is complex; report gas in ETH
print("events", len(out), "unpriced", len(out)-len(priced))
anom=[o for o in priced if o["debt_usd"]<=0 or o["coll_usd"]>o["debt_usd"]*D("1.2")]
clean=[o for o in priced if o not in anom]
tot=sum(o["gross"] for o in clean); print("anomalies", len(anom), "| clean gross $", round(tot), "| gas ETH", round(sum(o["gas_eth"] for o in clean),4))
liq=Counter(); 
for o in clean: liq[o["liq"]]+=o["gross"]
print("liquidators", len(liq), "top1", f"{max(liq.values())*100/tot:.0f}%", "top3", f"{sum(sorted(liq.values())[-3:])*100/tot:.0f}%")
m=defaultdict(D); 
for o in clean: m[time.strftime('%Y-%m',time.gmtime(o["ts"]))]+=o["gross"]
print("gross by month $:", {k: round(v) for k,v in sorted(m.items())})
bonus=sorted(o["gross"]/o["debt_usd"] for o in clean if o["debt_usd"]>0); print("bonus median", f"{bonus[len(bonus)//2]*100:.2f}%", "p90", f"{bonus[int(len(bonus)*0.9)]*100:.2f}%")
print("by spoke:", Counter(o["spoke"][:10] for o in clean).most_common())
