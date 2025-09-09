from modules.wallet import Wallet
from modules.browser import Browser
from modules.config import CHAIN_TOKENS


class Odos:
    def __init__(self, wallet: Wallet, browser: Browser, token_data: dict):
        self.wallet = wallet
        self.browser = browser

        self.token_data = token_data

        self.web3 = self.wallet.get_web3(self.token_data["chain"])
        self.chain_id = None

        self.token_output = {
            "address": "0x0000000000000000000000000000000000000000",
            "name": CHAIN_TOKENS[self.token_data["chain"]]
        }


    async def prepare_swap(self):
        self.chain_id = await self.web3.eth.chain_id
        odos_contract = await self.browser.odos_get_contract(chain_id=self.chain_id)

        odos_quote = await self.browser.odos_quote(
            token_address=self.token_data["address"],
            value=self.token_data["value"],
            chain_id=self.chain_id,
            token_output=self.token_output["address"]
        )

        await self.wallet.approve(
            chain_name=self.token_data["chain"],
            token_name=self.token_data["symbol"],
            spender=odos_contract,
            value=self.token_data["value"]
        )

        status = await self.swap(odos_quote=odos_quote)
        if type(status) == bool: return status


    async def swap(self, odos_quote: dict):
        tx_label = f'{self.token_data["chain"].upper()} odos swap {round(self.token_data["amount"], 2)} ' \
                     f'{self.token_data["symbol"]} -> {round(odos_quote["amount_out"], 6)} {self.token_output["name"]}'
        try:
            odos_tx = await self.browser.odos_assemble(path_id=odos_quote["path_id"])
            tx = {
                'from': self.wallet.address,
                'to': odos_tx["to"],
                'data': odos_tx["data"],
                'chainId': self.chain_id,
                'nonce': await self.web3.eth.get_transaction_count(self.wallet.address),
            }

            await self.wallet.sent_tx(
                chain_name=self.token_data["chain"],
                tx=tx,
                tx_label=tx_label,
                tx_raw=True
            )
            return True

        except Exception as error:
            raise ValueError(f'{tx_label}: {error}')
