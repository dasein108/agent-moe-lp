from __future__ import annotations

ERC20_ABI = [
    {
        "constant": True,
        "inputs": [],
        "name": "name",
        "outputs": [{"name": "", "type": "string"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "symbol",
        "outputs": [{"name": "", "type": "string"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [{"name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "spender", "type": "address"},
        ],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "constant": False,
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]

WMNT_ABI = ERC20_ABI + [
    {
        "inputs": [],
        "name": "deposit",
        "outputs": [],
        "stateMutability": "payable",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "uint256", "name": "wad", "type": "uint256"}],
        "name": "withdraw",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]

LB_PAIR_ABI = [
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "sender", "type": "address"},
            {"indexed": True, "name": "from", "type": "address"},
            {"indexed": True, "name": "to", "type": "address"},
            {"indexed": False, "name": "ids", "type": "uint256[]"},
            {"indexed": False, "name": "amounts", "type": "uint256[]"},
        ],
        "name": "TransferBatch",
        "type": "event",
    },
    {
        "inputs": [],
        "name": "getTokenX",
        "outputs": [{"internalType": "address", "name": "tokenX", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "getTokenY",
        "outputs": [{"internalType": "address", "name": "tokenY", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "getBinStep",
        "outputs": [{"internalType": "uint16", "name": "", "type": "uint16"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "getReserves",
        "outputs": [
            {"internalType": "uint128", "name": "reserveX", "type": "uint128"},
            {"internalType": "uint128", "name": "reserveY", "type": "uint128"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "getActiveId",
        "outputs": [{"internalType": "uint24", "name": "activeId", "type": "uint24"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "address", "name": "spender", "type": "address"},
            {"internalType": "bool", "name": "approved", "type": "bool"},
        ],
        "name": "approveForAll",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "address", "name": "owner", "type": "address"},
            {"internalType": "address", "name": "spender", "type": "address"},
        ],
        "name": "isApprovedForAll",
        "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "uint24", "name": "id", "type": "uint24"}],
        "name": "getBin",
        "outputs": [
            {"internalType": "uint128", "name": "binReserveX", "type": "uint128"},
            {"internalType": "uint128", "name": "binReserveY", "type": "uint128"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "getProtocolFees",
        "outputs": [
            {"internalType": "uint128", "name": "protocolFeeX", "type": "uint128"},
            {"internalType": "uint128", "name": "protocolFeeY", "type": "uint128"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "getStaticFeeParameters",
        "outputs": [
            {"internalType": "uint16", "name": "baseFactor", "type": "uint16"},
            {"internalType": "uint16", "name": "filterPeriod", "type": "uint16"},
            {"internalType": "uint16", "name": "decayPeriod", "type": "uint16"},
            {"internalType": "uint16", "name": "reductionFactor", "type": "uint16"},
            {
                "internalType": "uint24",
                "name": "variableFeeControl",
                "type": "uint24",
            },
            {"internalType": "uint16", "name": "protocolShare", "type": "uint16"},
            {
                "internalType": "uint24",
                "name": "maxVolatilityAccumulator",
                "type": "uint24",
            },
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "getVariableFeeParameters",
        "outputs": [
            {
                "internalType": "uint24",
                "name": "volatilityAccumulator",
                "type": "uint24",
            },
            {
                "internalType": "uint24",
                "name": "volatilityReference",
                "type": "uint24",
            },
            {"internalType": "uint24", "name": "idReference", "type": "uint24"},
            {"internalType": "uint40", "name": "timeOfLastUpdate", "type": "uint40"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "uint24", "name": "id", "type": "uint24"}],
        "name": "getPriceFromId",
        "outputs": [{"internalType": "uint256", "name": "price", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "uint256", "name": "id", "type": "uint256"}],
        "name": "totalSupply",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "address", "name": "account", "type": "address"},
            {"internalType": "uint256", "name": "id", "type": "uint256"},
        ],
        "name": "balanceOf",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "address[]", "name": "accounts", "type": "address[]"},
            {"internalType": "uint256[]", "name": "ids", "type": "uint256[]"},
        ],
        "name": "balanceOfBatch",
        "outputs": [{"internalType": "uint256[]", "name": "", "type": "uint256[]"}],
        "stateMutability": "view",
        "type": "function",
    },
]

LB_ROUTER_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "pair", "type": "address"},
            {"internalType": "uint128", "name": "amountIn", "type": "uint128"},
            {"internalType": "bool", "name": "swapForY", "type": "bool"},
        ],
        "name": "getSwapOut",
        "outputs": [
            {"internalType": "uint128", "name": "amountInLeft", "type": "uint128"},
            {"internalType": "uint128", "name": "amountOut", "type": "uint128"},
            {"internalType": "uint128", "name": "fee", "type": "uint128"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "uint256[]", "name": "pairBinSteps", "type": "uint256[]"},
                    {"internalType": "uint8[]", "name": "versions", "type": "uint8[]"},
                    {"internalType": "address[]", "name": "tokenPath", "type": "address[]"},
                ],
                "internalType": "struct ILBRouter.Path",
                "name": "path",
                "type": "tuple",
            },
        ],
        "name": "getSwapIn",
        "outputs": [
            {"internalType": "uint128", "name": "amountIn", "type": "uint128"},
            {"internalType": "uint128", "name": "amountOutLeft", "type": "uint128"},
            {"internalType": "uint128", "name": "fee", "type": "uint128"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
            {"internalType": "uint256", "name": "amountOutMin", "type": "uint256"},
            {
                "components": [
                    {"internalType": "uint256[]", "name": "pairBinSteps", "type": "uint256[]"},
                    {"internalType": "uint8[]", "name": "versions", "type": "uint8[]"},
                    {"internalType": "address[]", "name": "tokenPath", "type": "address[]"},
                ],
                "internalType": "struct ILBRouter.Path",
                "name": "path",
                "type": "tuple",
            },
            {"internalType": "address", "name": "to", "type": "address"},
            {"internalType": "uint256", "name": "deadline", "type": "uint256"},
        ],
        "name": "swapExactTokensForTokens",
        "outputs": [{"internalType": "uint256", "name": "amountOut", "type": "uint256"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "address", "name": "tokenX", "type": "address"},
                    {"internalType": "address", "name": "tokenY", "type": "address"},
                    {"internalType": "uint16", "name": "binStep", "type": "uint16"},
                    {"internalType": "uint256", "name": "amountX", "type": "uint256"},
                    {"internalType": "uint256", "name": "amountY", "type": "uint256"},
                    {"internalType": "uint256", "name": "amountXMin", "type": "uint256"},
                    {"internalType": "uint256", "name": "amountYMin", "type": "uint256"},
                    {"internalType": "uint256", "name": "activeIdDesired", "type": "uint256"},
                    {"internalType": "uint256", "name": "idSlippage", "type": "uint256"},
                    {"internalType": "int256[]", "name": "deltaIds", "type": "int256[]"},
                    {"internalType": "uint256[]", "name": "distributionX", "type": "uint256[]"},
                    {"internalType": "uint256[]", "name": "distributionY", "type": "uint256[]"},
                    {"internalType": "address", "name": "to", "type": "address"},
                    {"internalType": "address", "name": "refundTo", "type": "address"},
                    {"internalType": "uint256", "name": "deadline", "type": "uint256"},
                ],
                "internalType": "struct ILBRouter.LiquidityParameters",
                "name": "liquidityParameters",
                "type": "tuple",
            }
        ],
        "name": "addLiquidity",
        "outputs": [
            {"internalType": "uint256", "name": "amountXAdded", "type": "uint256"},
            {"internalType": "uint256", "name": "amountYAdded", "type": "uint256"},
            {"internalType": "uint256", "name": "amountXLeft", "type": "uint256"},
            {"internalType": "uint256", "name": "amountYLeft", "type": "uint256"},
            {"internalType": "uint256[]", "name": "depositIds", "type": "uint256[]"},
            {"internalType": "uint256[]", "name": "liquidityMinted", "type": "uint256[]"},
        ],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "address", "name": "tokenX", "type": "address"},
                    {"internalType": "address", "name": "tokenY", "type": "address"},
                    {"internalType": "uint256", "name": "binStep", "type": "uint256"},
                    {"internalType": "uint256", "name": "amountX", "type": "uint256"},
                    {"internalType": "uint256", "name": "amountY", "type": "uint256"},
                    {"internalType": "uint256", "name": "amountXMin", "type": "uint256"},
                    {"internalType": "uint256", "name": "amountYMin", "type": "uint256"},
                    {"internalType": "uint256", "name": "activeIdDesired", "type": "uint256"},
                    {"internalType": "uint256", "name": "idSlippage", "type": "uint256"},
                    {"internalType": "int256[]", "name": "deltaIds", "type": "int256[]"},
                    {"internalType": "uint256[]", "name": "distributionX", "type": "uint256[]"},
                    {"internalType": "uint256[]", "name": "distributionY", "type": "uint256[]"},
                    {"internalType": "address", "name": "to", "type": "address"},
                    {"internalType": "address", "name": "refundTo", "type": "address"},
                    {"internalType": "uint256", "name": "deadline", "type": "uint256"},
                ],
                "internalType": "struct ILBRouter.LiquidityParameters",
                "name": "liquidityParameters",
                "type": "tuple",
            }
        ],
        "name": "addLiquidityNATIVE",
        "outputs": [
            {"internalType": "uint256", "name": "amountXAdded", "type": "uint256"},
            {"internalType": "uint256", "name": "amountYAdded", "type": "uint256"},
            {"internalType": "uint256", "name": "amountXLeft", "type": "uint256"},
            {"internalType": "uint256", "name": "amountYLeft", "type": "uint256"},
            {"internalType": "uint256[]", "name": "depositIds", "type": "uint256[]"},
            {"internalType": "uint256[]", "name": "liquidityMinted", "type": "uint256[]"},
        ],
        "stateMutability": "payable",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "address", "name": "tokenX", "type": "address"},
            {"internalType": "address", "name": "tokenY", "type": "address"},
            {"internalType": "uint16", "name": "binStep", "type": "uint16"},
            {"internalType": "uint256", "name": "amountXMin", "type": "uint256"},
            {"internalType": "uint256", "name": "amountYMin", "type": "uint256"},
            {"internalType": "uint256[]", "name": "ids", "type": "uint256[]"},
            {"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"},
            {"internalType": "address", "name": "to", "type": "address"},
            {"internalType": "uint256", "name": "deadline", "type": "uint256"},
        ],
        "name": "removeLiquidity",
        "outputs": [
            {"internalType": "uint256", "name": "amountX", "type": "uint256"},
            {"internalType": "uint256", "name": "amountY", "type": "uint256"},
        ],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]

LB_FACTORY_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "tokenA", "type": "address"},
            {"internalType": "address", "name": "tokenB", "type": "address"},
            {"internalType": "uint256", "name": "binStep", "type": "uint256"},
        ],
        "name": "getLBPairInformation",
        "outputs": [
            {
                "components": [
                    {"internalType": "uint16", "name": "binStep", "type": "uint16"},
                    {"internalType": "address", "name": "LBPair", "type": "address"},
                    {"internalType": "bool", "name": "createdByOwner", "type": "bool"},
                    {"internalType": "bool", "name": "ignoredForRouting", "type": "bool"},
                ],
                "internalType": "struct ILBFactory.LBPairInformation",
                "name": "lbPairInformation",
                "type": "tuple",
            }
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "address", "name": "tokenX", "type": "address"},
            {"internalType": "address", "name": "tokenY", "type": "address"},
        ],
        "name": "getAllLBPairs",
        "outputs": [
            {
                "components": [
                    {"internalType": "uint16", "name": "binStep", "type": "uint16"},
                    {"internalType": "address", "name": "LBPair", "type": "address"},
                    {"internalType": "bool", "name": "createdByOwner", "type": "bool"},
                    {"internalType": "bool", "name": "ignoredForRouting", "type": "bool"},
                ],
                "internalType": "struct ILBFactory.LBPairInformation[]",
                "name": "lbPairsAvailable",
                "type": "tuple[]",
            }
        ],
        "stateMutability": "view",
        "type": "function",
    },
]
