import os
import time
import numpy as np
from scipy.optimize import minimize
from web3 import Web3
from web3.middleware import geth_poa_middleware
from dotenv import load_dotenv

load_dotenv()

# Simple mock ABIs for the vault
VAULT_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "strategyAdapter", "type": "address"},
            {"internalType": "uint256", "name": "amount", "type": "uint256"}
        ],
        "name": "rebalance",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    }
]

class QuantAllocationEngine:
    def __init__(self, gas_cost_usd, slippage_tolerance, max_allocation_pct):
        self.gas_cost_usd = gas_cost_usd
        self.slippage_tolerance = slippage_tolerance
        self.max_allocation_pct = max_allocation_pct
        self.alpha_apy = 0.40
        self.beta_tvl = 0.20
        self.gamma_scr = 0.25
        self.delta_vol = 0.15

    def compute_utility_score(self, apy, tvl, scr, vol):
        norm_apy = apy / 20.0
        norm_tvl = min(tvl / 100_000_000, 1.0)
        norm_scr = scr / 10.0
        norm_vol = max(0, 1.0 - vol)
        return (self.alpha_apy * norm_apy) + (self.beta_tvl * norm_tvl) - (self.gamma_scr * (1 - norm_scr)) - (self.delta_vol * (1 - norm_vol))

    def optimize_allocations(self, current_allocations, strategies, total_portfolio_usd):
        n = len(strategies)
        scores = np.array([self.compute_utility_score(s['apy'], s['tvl'], s['scr'], s['vol']) for s in strategies])
        
        def objective(x): return -np.sum(x * scores)
        cons = ({'type': 'eq', 'fun': lambda x: np.sum(x) - 1.0})
        bounds = tuple((0.0, self.max_allocation_pct) for _ in range(n))
        x0 = np.array([1.0 / n] * n)
        
        res = minimize(objective, x0, method='SLSQP', bounds=bounds, constraints=cons)
        optimal_allocations = res.x
        
        current_yield_usd = sum((c * total_portfolio_usd) * s['apy'] / 100 for c, s in zip(current_allocations, strategies))
        new_yield_usd = sum((o * total_portfolio_usd) * s['apy'] / 100 for o, s in zip(optimal_allocations, strategies))
        delta_yield = new_yield_usd - current_yield_usd
        
        if delta_yield > (self.gas_cost_usd * 3):
            return {"execute": True, "allocations": optimal_allocations, "expected_yield_increase": delta_yield}
        return {"execute": False, "allocations": current_allocations, "reason": "Yield delta too low to justify gas costs."}

def main():
    RPC_URL = os.environ.get("BASE_RPC_URL", "https://mainnet.base.org")
    PRIVATE_KEY = os.environ.get("KEEPER_PRIVATE_KEY")
    VAULT_ADDRESS = os.environ.get("VAULT_ADDRESS", "0x2C5bb4BF97BCdeF02EcD3b89a7E428164A9D2fcb")
    
    # Static strategy list for this example
    strategies = [
        {"name": "Aerodrome V2", "address": "0x5A0E... (Mock)", "apy": 14.2, "tvl": 124_000_000, "scr": 8, "vol": 0.15},
        {"name": "Moonwell", "address": "0x5A0E... (Mock)", "apy": 8.4, "tvl": 68_000_000, "scr": 9, "vol": 0.05},
        {"name": "Aave V3", "address": "0x5A0E... (Mock)", "apy": 6.1, "tvl": 95_000_000, "scr": 9, "vol": 0.02},
        {"name": "Compound III", "address": "0x5A0E... (Mock)", "apy": 5.8, "tvl": 45_000_000, "scr": 8, "vol": 0.04},
    ]

    print("====================================")
    print("Starting Heroku AI Keeper Worker...")
    print("====================================")
    
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    w3.middleware_onion.inject(geth_poa_middleware, layer=0)
    
    if not w3.is_connected():
        print("Failed to connect to Base mainnet RPC.")
        return

    account = None
    if PRIVATE_KEY:
        account = w3.eth.account.from_key(PRIVATE_KEY)
        print(f"Loaded Wallet: {account.address}")
    else:
        print("WARNING: No KEEPER_PRIVATE_KEY configured. Running in Dry-Run mode.")

    engine = QuantAllocationEngine(gas_cost_usd=0.02, slippage_tolerance=0.005, max_allocation_pct=0.40)
    vault_contract = w3.eth.contract(address=w3.to_checksum_address(VAULT_ADDRESS), abi=VAULT_ABI)

    while True:
        try:
            print(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Running allocation cycle on Base...")
            
            # Simulated current allocations (25% in each)
            current = [0.25, 0.25, 0.25, 0.25] 
            
            # Using a dummy TVL magnitude for yield modeling. You would usually query vault.totalAssets()
            total_portfolio_usd = 500_000 

            result = engine.optimize_allocations(current, strategies, total_portfolio_usd)
            
            if result["execute"]:
                print(">> Strategy shift required based on Quant Model!")
                print(f">> Expected Net Yield Increase (Annualized): ${result['expected_yield_increase']:.2f}")
                
                # Identify the optimal strategy array maximum 
                best_idx = np.argmax(result['allocations'])
                target_strategy = strategies[best_idx]
                
                print(f">> Pivoting portfolio toward: {target_strategy['name']}")

                if account:
                    # Execute transaction towards Base Network
                    nonce = w3.eth.get_transaction_count(account.address)
                    print(f"Constructing transaction with Nonce: {nonce}...")
                    
                    tx = vault_contract.functions.rebalance(
                        w3.to_checksum_address("0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE"), # In production: target_strategy['address']
                        int(total_portfolio_usd * 1_000_000)
                    ).build_transaction({
                        'chainId': 8453, # Base Mainnet CID
                        'gas': 500000,   # Simulated Gas Limit
                        'maxFeePerGas': w3.to_wei(0.1, 'gwei'),
                        'maxPriorityFeePerGas': w3.to_wei(0.01, 'gwei'),
                        'nonce': nonce,
                    })

                    print("Signing and sending transaction...")
                    signed_tx = w3.eth.account.sign_transaction(tx, private_key=PRIVATE_KEY)
                    tx_hash = w3.eth.send_raw_transaction(signed_tx.rawTransaction)
                    print(f"Transaction broadcasted! Tx Hash: {w3.to_hex(tx_hash)}")
            else:
                print(f"No execution needed: {result['reason']}")
                
        except Exception as e:
            print(f"Error during cycle: {e}")

        print("Sleeping for 1 hour...")
        time.sleep(3600)  # Rest the bot before next loop

if __name__ == "__main__":
    main()
