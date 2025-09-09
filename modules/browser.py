from aiohttp import ClientSession
from loguru import logger

from modules import DataBase
from modules.retry import retry, have_json
from settings import AFTER_CLAIM


class Browser:
    def __init__(self, proxy: str, encoded_pk: str, address: str, db: DataBase):
        self.max_retries = 5
        self.encoded_pk = encoded_pk
        self.address = address
        self.db = db

        if proxy not in ['https://log:pass@ip:port', 'http://log:pass@ip:port', 'log:pass@ip:port', '', None]:
            self.proxy = "http://" + proxy.removeprefix("https://").removeprefix("http://")
        else:
            self.proxy = None

        if self.proxy:
            logger.opt(colors=True).debug(f'[•] <white>{self.address}</white> | Got proxy {self.proxy}')
        else:
            logger.opt(colors=True).warning(f'[-] <white>{self.address}</white> | Dont use proxies!')

        self.sessions = []
        self.session = self.get_new_session()


    def get_new_session(self):
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
            "Origin": "https://app.odos.xyz",
            "Referer": "https://app.odos.xyz/",
        }

        session = ClientSession(headers=headers)
        session.proxy = self.proxy

        self.sessions.append(session)
        return session


    async def close_sessions(self):
        for session in self.sessions:
            await session.close()


    @have_json
    async def send_request(self, **kwargs):
        if kwargs.get("session"):
            session = kwargs["session"]
            del kwargs["session"]
        else:
            session = self.session

        if kwargs.get("method"): kwargs["method"] = kwargs["method"].upper()
        if self.proxy:
            kwargs["proxy"] = self.proxy
        return await session.request(**kwargs)


    async def odos_get_contract(self, chain_id: int):
        try:
            r = await self.send_request(
                method="GET",
                url=f'https://api.odos.xyz/info/contract-info/v3/{chain_id}',
            )
            return (await r.json())["routerAddress"]

        except Exception as err:
            if "!DOCTYPE HTML" in str(err): err = "work only with proxy"
            raise Exception(f"Coudlnt get odos contract: {err}")


    async def odos_quote(self, token_address: str, value: int, chain_id: int, token_output: str):
        try:
            payload = {
                "chainId": chain_id,
                "inputTokens": [{
                        "tokenAddress": token_address,
                        "amount": str(value)
                }],
                "outputTokens": [{
                        "tokenAddress": token_output,
                        "proportion": 1
                }],
                "slippageLimitPercent": AFTER_CLAIM["slippage"],
                "sourceBlacklist": [],
                "pathViz": False,
                "referralCode": 1,
                "compact": True,
                "likeAsset": True,
                "disableRFQs": False,
                "userAddr": self.address,
            }
            r = await self.send_request(
                method="POST",
                url='https://api.odos.xyz/sor/quote/v3',
                json=payload,
            )
            response = await r.json()
            if r.status != 200: raise ValueError(f'sor: {response}')

            return {
                "path_id": response['pathId'],
                "amount_out": round(int(response["outAmounts"][0]) / 1e18, 6),
            }

        except Exception as err:
            raise Exception(f"Coudlnt get odos quote: {err}")


    async def odos_assemble(self, path_id: str):
        try:
            r = await self.send_request(
                method="POST",
                url='https://api.odos.xyz/sor/assemble',
                json={
                    "userAddr": self.address,
                    "pathId": path_id,
                    "simulate": True
                },
            )
            response = await r.json()
            if response.get("simulation") is None:
                raise Exception(f'bad assemble response {response}')
            elif response['simulation']['isSuccess'] is not True:
                if response['simulation']['simulationError']['type'] == "other":
                    raise Exception(f'simulation failed {response["simulation"]["simulationError"]["errorMessage"]}')
                else:
                    raise Exception(f'simulation failed {response["simulation"]["simulationError"]}')

            return {
                "data": response['transaction']['data'],
                "to": response['transaction']['to'],
            }

        except Exception as err:
            if "!DOCTYPE HTML" in str(err): err = "work only with proxy"
            raise Exception(f"Coudlnt get odos assemble: {err}")
