from eth_account.messages import (
    encode_defunct,
    encode_typed_data,
    _hash_eip191_message
)
from random import uniform, randint
from typing import Union, Optional
from decimal import Decimal
from loguru import logger
from web3.auto import w3
from time import time
import asyncio

from modules.rpc_initializer import RPCInitializer
from modules.retry import TransactionError, CustomError
from modules.multicall import MultiCall
from modules.utils import async_sleep
from modules.database import DataBase
import modules.config as config
import settings

from requests.exceptions import HTTPError
from web3.exceptions import ContractLogicError, BadFunctionCallOutput


class Wallet:
    def __init__(
            self,
            privatekey: str,
            encoded_pk: str,
            recipient: str | None,
            db: DataBase,
    ):
        self.privatekey = privatekey
        self.encoded_pk = encoded_pk
        self.db = db

        self.account = w3.eth.account.from_key(privatekey) if privatekey else None
        self.address = self.account.address if privatekey else None
        self.recipient = w3.to_checksum_address(recipient) if recipient else None


    def get_web3(self, chain_name: str):
        return RPCInitializer.get_rpc(chain_name)

    async def wait_for_gwei(self):
        for chain_data in [
            {'chain_name': 'ethereum', 'max_gwei': settings.ETH_MAX_GWEI},
        ]:
            first_check = True
            while True:
                try:
                    new_gwei = round((await self.get_web3(chain_name=chain_data['chain_name']).eth.gas_price) / 10 ** 9, 2)
                    if new_gwei < chain_data["max_gwei"]:
                        if not first_check:
                            logger.debug(f'[•] {self.address} | New {chain_data["chain_name"].title()} GWEI is {new_gwei}')
                        break
                    await async_sleep(5)
                    if first_check:
                        first_check = False
                        logger.debug(f'[•] {self.address} | Waiting for GWEI in {chain_data["chain_name"].title()} at least {chain_data["max_gwei"]}. Current is {new_gwei}')
                except Exception as err:
                    logger.warning(f'[•] {self.address} | {chain_data["chain_name"].title()} gwei waiting error: {err}')
                    await async_sleep(10)


    async def get_gas(self, chain_name: str, increasing_gwei: float = 0):
        web3 = self.get_web3(chain_name=chain_name)

        max_priority, last_block, gas_price = await asyncio.gather(*[
            web3.eth.max_priority_fee,
            web3.eth.get_block('latest'),
            web3.eth.gas_price,
        ])

        base_fee = int(max(last_block['baseFeePerGas'], gas_price) * (settings.GWEI_MULTIPLIER + increasing_gwei))
        block_filled = last_block['gasUsed'] / last_block['gasLimit'] * 100
        if block_filled > 50: base_fee = int(base_fee * 1.127)

        max_fee = int(base_fee + int(max_priority))
        return {'maxPriorityFeePerGas': max_fee, 'maxFeePerGas': max_fee}


    async def sent_tx(
            self,
            chain_name: str,
            tx,
            tx_label: str,
            tx_raw: bool = False,
            value: int = 0,
            increasing_gwei: float = 0,
            force_gas: float = 0,
    ):
        try:
            web3 = self.get_web3(chain_name=chain_name)
            if not tx_raw:
                chain_id, nonce, gas_params = await asyncio.gather(*[
                    web3.eth.chain_id,
                    web3.eth.get_transaction_count(self.address),
                    self.get_gas(chain_name=chain_name, increasing_gwei=increasing_gwei),
                ])

                tx_raw_data = {
                    'from': self.address,
                    'chainId': chain_id,
                    'nonce': nonce,
                    'value': value,
                    **gas_params,
                }
                if force_gas:
                    tx_raw_data['gas'] = force_gas

                tx_completed = await tx.build_transaction(tx_raw_data)
            else:
                tx_completed = {
                    **tx,
                    **await self.get_gas(chain_name=chain_name, increasing_gwei=increasing_gwei),
                }
                tx_completed["gas"] = await web3.eth.estimate_gas(tx_completed)
                if force_gas:
                    tx_completed["gas"] = force_gas

            signed_tx = web3.eth.account.sign_transaction(tx_completed, self.privatekey)

            raw_tx_hash = await web3.eth.send_raw_transaction(signed_tx.rawTransaction)
            tx_hash = web3.to_hex(raw_tx_hash)
            return await self.wait_for_tx(chain_name, tx_hash, tx_label)

        except Exception as err:
            try: encoded_tx = tx_completed._encode_transaction_data()
            except: encoded_tx = ''
            raise TransactionError(f'tx failed error', error_code=str(err), encoded_tx=encoded_tx)


    async def wait_for_tx(self, chain_name: str, tx_hash: str, tx_label: str):
        web3 = self.get_web3(chain_name)
        tx_link = f'{config.CHAINS_DATA[chain_name]["explorer"]}{tx_hash}'
        logger.debug(f'[•] {self.address} | {tx_label} tx sent: {tx_link}')

        while True:
            try:
                status = (await web3.eth.wait_for_transaction_receipt(tx_hash, timeout=int(settings.TO_WAIT_TX * 60))).status
                break

            except HTTPError as err:
                logger.error(f'[-] {self.address} | Coudlnt get TX, probably you need to change RPC ({web3.provider.endpoint_uri}): {err}')
                await async_sleep(5)

        if status == 1:
            logger.success(f'[+] {self.address} | {tx_label} tx confirmed')
            await self.db.append_report(
                encoded_pk=self.encoded_pk,
                text=tx_label,
                success=True
            )
            return tx_hash
        else:
            await self.db.append_report(
                encoded_pk=self.encoded_pk,
                text=f'{tx_label} | tx is failed | <a href="{tx_link}">link 👈</a>',
                success=False
            )
            raise ValueError(f'tx failed: {tx_link}')


    async def approve(
            self,
            chain_name: str,
            token_name: str,
            spender: str,
            amount: float = None,
            value: int = None,
    ):
        "approve only if not approved"

        token_contract = self.get_web3(chain_name=chain_name).eth.contract(
            address=config.TOKEN_ADDRESSES[chain_name][token_name],
            abi='[{"inputs":[{"internalType":"address","name":"owner","type":"address"},{"internalType":"address","name":"spender","type":"address"}],"name":"allowance","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"spender","type":"address"},{"internalType":"uint256","name":"value","type":"uint256"}],"name":"approve","outputs":[{"internalType":"bool","name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"},{"inputs":[],"name":"decimals","outputs":[{"internalType":"uint8","name":"","type":"uint8"}],"stateMutability":"view","type":"function"}]',
        )
        decimals = await token_contract.functions.decimals().call()

        if amount:
            value = int(amount * 10 ** decimals)
        elif value:
            amount = round(value / 10 ** decimals, 5)

        if value == 0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff:
            min_allowance = 0xfffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff
            amount = "infinity"
        else:
            min_allowance = value

        if await token_contract.functions.allowance(
            self.address,
            spender
        ).call() < min_allowance:
            module_str = f"approve {amount} {token_name}"
            tx = token_contract.functions.approve(
                spender,
                value
            )
            await self.sent_tx(chain_name=chain_name, tx=tx, tx_label=module_str)
            return True

        else:
            return False


    async def get_balance(
            self,
            chain_name: str,
            token_name: str = False,
            token_address: bool = False,
            human: bool = False,
            tokenId: int = None
    ):
        web3 = self.get_web3(chain_name=chain_name)
        if token_name: token_address = config.TOKEN_ADDRESSES[chain_name][token_name]
        if token_address:
            contract = web3.eth.contract(
                address=web3.to_checksum_address(token_address),
                abi='[{"inputs":[{"internalType":"address","name":"account","type":"address"}],"name":"balanceOf","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"spender","type":"address"},{"internalType":"uint256","name":"amount","type":"uint256"}],"name":"approve","outputs":[{"internalType":"bool","name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"internalType":"address","name":"owner","type":"address"},{"internalType":"address","name":"spender","type":"address"}],"name":"allowance","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"decimals","outputs":[{"internalType":"uint8","name":"","type":"uint8"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"","type":"address"},{"internalType":"uint256","name":"","type":"uint256"}],"name":"balanceOf","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function"}]'
            )

        while True:
            try:
                if token_address:
                    if tokenId is not None:
                        if type(tokenId) != list:
                            params = [self.address, tokenId]
                        else:
                            param = tokenId[0]
                            if param is None:
                                params = [self.address]
                            else:
                                params = [self.address, param]
                    else:
                        params = [self.address]
                    balance = await contract.functions.balanceOf(*params).call()
                else: balance = await web3.eth.get_balance(self.address)

                if not human: return balance

                decimals = await contract.functions.decimals().call() if token_address else 18
                return balance / 10 ** decimals

            except ContractLogicError:
                if type(tokenId) == list and len(tokenId) != 0:
                    tokenId.pop(0)

                elif tokenId is not None:
                    tokenId = None
                    continue

                if (
                        type(tokenId) == list and len(tokenId) == 0
                        or
                        type(tokenId) is not list
                ):
                    raise

            except BadFunctionCallOutput:
                logger.warning(f'[-] {self.address} | Bad address to get balance ({web3.provider.endpoint_uri}): {token_address}')
                return None

            except Exception as err:
                logger.warning(f'[•] {self.address} | Get {token_address} balance error ({tokenId}) ({web3.provider.endpoint_uri}): {err}')
                await async_sleep(5)


    async def get_token_info(
            self,
            chain_name: str,
            token_name: str = False,
            token_address: str = False,
    ):
        web3 = self.get_web3(chain_name=chain_name)
        native_token = config.CHAIN_TOKENS[chain_name]
        if token_name and token_name != native_token: token_address = config.TOKEN_ADDRESSES[chain_name][token_name]
        if token_address:
            token_address = web3.to_checksum_address(token_address)
            contract = web3.eth.contract(
                address=token_address,
                abi='[{"inputs":[{"internalType":"address","name":"account","type":"address"}],"name":"balanceOf","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"spender","type":"address"},{"internalType":"uint256","name":"amount","type":"uint256"}],"name":"approve","outputs":[{"internalType":"bool","name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"internalType":"address","name":"owner","type":"address"},{"internalType":"address","name":"spender","type":"address"}],"name":"allowance","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"decimals","outputs":[{"internalType":"uint8","name":"","type":"uint8"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"","type":"address"},{"internalType":"uint256","name":"","type":"uint256"}],"name":"balanceOf","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"symbol","outputs":[{"internalType":"string","name":"","type":"string"}],"stateMutability":"view","type":"function"}]'
            )

        while True:
            try:
                if token_address:
                    call_data = {
                        "balanceOf": {"contract": contract, "func": "balanceOf", "args": [self.address]},
                        "decimals": {"contract": contract, "func": "decimals", "args": []},
                        "symbol": {"contract": contract, "func": "symbol", "args": []},
                    }
                    call_resp = await MultiCall.call(chain_name=chain_name, call_data=call_data)
                    balance, decimals, symbol = call_resp["balanceOf"], call_resp["decimals"], call_resp["symbol"]

                else:
                    balance = await web3.eth.get_balance(self.address)
                    decimals = 18
                    symbol = native_token
                    token_address = "0x0000000000000000000000000000000000000000"

                return {
                    "value": balance,
                    "amount": balance / 10 ** decimals,
                    "decimals": decimals,
                    "symbol": symbol,
                    "address": token_address,
                }

            except BadFunctionCallOutput:
                logger.warning(f'[-] {self.address} | Bad address to get balance ({web3.provider.endpoint_uri}): {token_address}')
                return None

            except Exception as err:
                logger.warning(f'[•] {self.address} | Get {token_address} balance error ({web3.provider.endpoint_uri}): {err}')
                await async_sleep(5)


    async def wait_balance(self,
                     chain_name: str,
                     needed_balance: Union[int, float],
                     only_more: bool = False,
                     token_name: Optional[str] = False,
                     token_address: Optional[str] = False,
                     human: bool = True,
                     timeout: int = 0
    ):
        " needed_balance: human digit "
        if token_name:
            token_address = config.TOKEN_ADDRESSES[chain_name][token_name]

        if token_address:
            contract = self.get_web3(chain_name=chain_name).eth.contract(address=w3.to_checksum_address(token_address),
                                         abi='[{"inputs":[{"internalType":"address","name":"account","type":"address"}],"name":"balanceOf","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"spender","type":"address"},{"internalType":"uint256","name":"amount","type":"uint256"}],"name":"approve","outputs":[{"internalType":"bool","name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"internalType":"address","name":"owner","type":"address"},{"internalType":"address","name":"spender","type":"address"}],"name":"allowance","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"decimals","outputs":[{"internalType":"uint8","name":"","type":"uint8"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"name","outputs":[{"internalType":"string","name":"","type":"string"}],"stateMutability":"view","type":"function"}]')
            token_name = await contract.functions.name().call()

        else:
            token_name = 'ETH'

        if only_more: logger.debug(f'[•] {self.address} | Waiting for balance more than {round(needed_balance, 6)} {token_name} in {chain_name.upper()}')
        else: logger.debug(f'[•] {self.address} | Waiting for {round(needed_balance, 6)} {token_name} balance in {chain_name.upper()}')
        start_time = time()

        while True:
            try:
                new_balance = await self.get_balance(chain_name=chain_name, human=human, token_address=token_address)

                if only_more: status = new_balance > needed_balance
                else: status = new_balance >= needed_balance
                if status:
                    logger.debug(f'[•] {self.address} | New balance: {round(new_balance, 6)} {token_name}')
                    return new_balance
                if timeout and time() - start_time > timeout:
                    logger.error(f'[-] {self.address} | No token found in {timeout} seconds')
                    return 0
                await async_sleep(5)
            except Exception as err:
                logger.warning(f'[•] {self.address} | Wait balance error: {err}')
                await async_sleep(10)


    def sign_message(
            self,
            text: str = None,
            typed_data: dict = None,
            hash: bool = False
    ):
        if text:
            message = encode_defunct(text=text)
        elif typed_data:
            message = encode_typed_data(full_message=typed_data)
            if hash:
                message = encode_defunct(hexstr=_hash_eip191_message(message).hex())

        signed_message = self.account.sign_message(message)
        signature = signed_message.signature.hex()
        if not signature.startswith('0x'): signature = '0x' + signature
        return signature


    async def transfer_token(self, chain_name: str, token_name: str, value: int):
        token_contract = RPCInitializer.initialize_contract(
            chain_name=chain_name,
            address=config.TOKEN_ADDRESSES[chain_name][token_name],
            abi='[{"inputs":[],"name":"decimals","outputs":[{"internalType":"uint8","name":"","type":"uint8"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"to","type":"address"},{"internalType":"uint256","name":"value","type":"uint256"}],"name":"transfer","outputs":[{"internalType":"bool","name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"}]',
        )
        decimals = await token_contract.functions.decimals().call()
        amount = round(value / 10 ** decimals, 5)

        module_str = f"transfer {amount} {token_name}"
        tx = token_contract.functions.transfer(
            self.recipient,
            value
        )
        await self.sent_tx(chain_name=chain_name, tx=tx, tx_label=module_str)
        return True


    async def transfer_native(self, chain_name: str, decrease: float = 0):
        keep_amounts = settings.AFTER_CLAIM["keep_eth"]
        native_amount = await self.get_balance(chain_name=chain_name, human=True) - decrease
        if native_amount < keep_amounts[0]:
            raise CustomError(f"Not enough ETH ({round(native_amount, 5)}) for minimal keep ETH balance ({keep_amounts[0]})")

        if keep_amounts[1] > native_amount:
            keep_amounts[1] = native_amount
        keep_amount = uniform(*keep_amounts)
        transfer_amount = round(native_amount - keep_amount, randint(5, 7))
        transfer_value = int(transfer_amount * 1e18)
        amount = round(transfer_value / 1e18, 5)
        tx_label = f"transfer {amount} ETH"

        web3 = self.get_web3(chain_name=chain_name)
        tx = {
            'from': self.address,
            'to': self.recipient,
            "value": transfer_value,
            'chainId': await web3.eth.chain_id,
            'nonce': await web3.eth.get_transaction_count(self.address),
        }

        try:
            await self.sent_tx(
                chain_name=chain_name,
                tx=tx,
                tx_label=tx_label,
                tx_raw=True
            )
        except Exception as err:
            if "insufficient funds for transfer" in str(err) or "gas required exceeds allowance" in str(err):
                logger.warning(f'[-] {self.address} | {tx_label} | insufficient funds, recalculating... (-{str(round(Decimal(decrease + 0.00001), 5))})')
                return await self.transfer_native(chain_name=chain_name, decrease=decrease + 0.00001)
            else:
                raise CustomError(f"Failed to {tx_label}: {err}")

        return True
