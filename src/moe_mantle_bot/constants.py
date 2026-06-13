from __future__ import annotations

MANTLE_MAINNET_CHAIN_ID = 5000
MANTLE_MAINNET_RPC_URL = "https://rpc.mantle.xyz"

# RPC endpoints for failover rotation
MANTLE_RPC_ENDPOINTS = [
    "https://rpc.mantle.xyz",
    "https://mantle-rpc.publicnode.com",
    "https://mantle.drpc.org",
    "https://1rpc.io/mantle",
    "https://mantle-mainnet.public.blastapi.io",
]

# Merchant Moe Liquidity Book V2.2 on Mantle
MOE_LB_FACTORY = "0xa6630671775c4EA2743840F9A5016dCf2A104054"
MOE_LB_ROUTER = "0x013e138EF6008ae5FDFDE29700e3f2Bc61d21E3a"
MOE_LB_QUOTER = "0x501b8AFd35df20f531fF45F6f695793AC3316c85"

# Default WMNT/USDT Merchant Moe LB pair. Multiple pairs may exist per token
# pair (different bin steps), so keep overrideable via POOL_ADDRESS.
WMNT_USDT_POOL = "0xf6c9020c9e915808481757779edb53daceae2415"
WMNT_TOKEN = "0x78c1b0c915c4faa5fffa6cabf0219da63d7f4cb8"
USDT_TOKEN = "0x201EBa5CC46D216Ce6DC03F6a759e8E766e956aE"

MANTLE_CHAIN_SLUG = "mantle"
