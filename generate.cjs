const fs = require('fs');
const abi = fs.readFileSync('dist_contracts/src_contracts_YieldPilotVault_sol_YieldPilotVault.abi', 'utf8');
const bin = fs.readFileSync('dist_contracts/src_contracts_YieldPilotVault_sol_YieldPilotVault.bin', 'utf8');
fs.writeFileSync('src/lib/DeployData.ts', `export const VaultABI = ${abi};\nexport const VaultBytecode = "0x${bin}";\n`);
