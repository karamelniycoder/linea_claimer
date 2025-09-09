from random import randint
from loguru import logger

from modules.rpc_initializer import RPCInitializer
from modules.retry import retry, TransactionError
from modules.config import TOKEN_ADDRESSES
from modules.multicall import MultiCall
from modules.utils import async_sleep
from modules.browser import Browser
from modules.wallet import Wallet
from modules.odos import Odos
from settings import SLEEP_AFTER_TX, AFTER_CLAIM


class Linea:

    def __init__(self, wallet: Wallet, browser: Browser):
        self.wallet = wallet
        self.browser = browser

        self.from_chain = "linea"
        self.web3 = self.wallet.get_web3(self.from_chain)


    @retry(source="Linea")
    async def run(self):
        to_sleep = False

        claim_contract = RPCInitializer.initialize_contract(
            chain_name=self.from_chain,
            address="0x87bAa1694381aE3eCaE2660d97fe60404080Eb64",
            abi='[{"inputs":[{"internalType":"address","name":"_account","type":"address"}],"name":"calculateAllocation","outputs":[{"internalType":"uint256","name":"tokenAllocation","type":"uint256"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"claim","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"internalType":"address","name":"user","type":"address"}],"name":"hasClaimed","outputs":[{"internalType":"bool","name":"claimed","type":"bool"}],"stateMutability":"view","type":"function"}]',
        )

        call_data = {
            "allocation_value": {
                "contract": claim_contract,
                "func": "calculateAllocation",
                "args": [self.wallet.address]
            },
            "is_claimed": {
                "contract": claim_contract,
                "func": "hasClaimed",
                "args": [self.wallet.address]
            },
        }
        call_resp = await MultiCall.call(chain_name=self.from_chain, call_data=call_data)
        call_resp["allocation_amount"] = round(call_resp["allocation_value"] / 1e18, 1)

        if call_resp["allocation_value"] == 0:
            self.log_message(f"Not eligible", level="INFO")
            await self.wallet.db.append_report(
                encoded_pk=self.wallet.encoded_pk,
                text="not eligible",
                success=False,
            )

        elif call_resp["allocation_value"] and call_resp["is_claimed"]:
            self.log_message(f"Already claimed {call_resp['allocation_amount']} LINEA", level="INFO")
            await self.wallet.db.append_report(
                encoded_pk=self.wallet.encoded_pk,
                text=f"already claimed {call_resp['allocation_amount']} LINEA",
                success=True,
            )

        elif call_resp["allocation_value"] and not call_resp["is_claimed"]:
            self.log_message(f"Eligible for {call_resp['allocation_amount']} LINEA", level="SUCCESS")
            await self.wallet.db.append_report(
                encoded_pk=self.wallet.encoded_pk,
                text=f"eligible for {call_resp['allocation_amount']} LINEA",
                success=True,
            )

            claim_status = await self.claim(value=call_resp["allocation_value"], contract=claim_contract)
            if claim_status is False:
                return False
            to_sleep = True

        linea_value = await self.wallet.get_balance(chain_name=self.from_chain, token_name="LINEA")
        if linea_value:
            if AFTER_CLAIM["swap"]:
                if to_sleep:
                    await async_sleep(randint(*SLEEP_AFTER_TX))

                await self.wallet.wait_for_gwei()
                await Odos(
                    wallet=self.wallet,
                    browser=self.browser,
                    token_data={
                        "symbol": "LINEA",
                        "address": TOKEN_ADDRESSES[self.from_chain]["LINEA"],
                        "chain": self.from_chain,
                        "value": linea_value,
                        "amount": linea_value / 1e18,
                    }
                ).prepare_swap()
                to_sleep = True

            elif AFTER_CLAIM["send_token"]:
                if to_sleep:
                    await async_sleep(randint(*SLEEP_AFTER_TX))
                await self.wallet.wait_for_gwei()
                await self.wallet.transfer_token(chain_name=self.from_chain, token_name="LINEA", value=linea_value)
                to_sleep = True

        if AFTER_CLAIM["send_eth"] and self.wallet.recipient:
            if to_sleep:
                await async_sleep(randint(*SLEEP_AFTER_TX))
            await self.wallet.wait_for_gwei()
            await self.wallet.transfer_native(chain_name=self.from_chain)

        return True


    async def claim(self, value: int, contract):
        amount = round(value / 1e18, 1)
        tx_label = f"claim {amount} LINEA"

        tx = contract.functions.claim()

        try:
            await self.wallet.sent_tx(
                chain_name=self.from_chain,
                tx=tx,
                tx_label=tx_label,
            )
        except TransactionError as err:
            if err.error_code.startswith("0xe450d38c"):
                self.log_message(f"Claim is not started yet", level="WARNING")
                await self.wallet.db.append_report(
                    encoded_pk=self.wallet.encoded_pk,
                    text=f"claim is not started yet",
                    success=False,
                )
                return False
            else:
                raise

        return True


    def log_message(
            self,
            text: str,
            smile: str = "•",
            level: str = "DEBUG",
            colors: bool = True
    ):
        label = f"<white>{self.wallet.address}</white>" if colors else self.wallet.address
        logger.opt(colors=colors).log(level.upper(), f'[{smile}] {label} | {text}')
