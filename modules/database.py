from random import choice, randint, shuffle
from cryptography.fernet import Fernet
from base64 import urlsafe_b64encode
from time import sleep, time
from os import path, mkdir
from loguru import logger
from hashlib import md5
import asyncio
import json

from .retry import DataBaseError
from modules import utils, WindowName
from settings import SHUFFLE_WALLETS, AFTER_CLAIM

from cryptography.fernet import InvalidToken


class DataBase:
    def __init__(self):

        self.modules_db_name = 'databases/modules.json'
        self.report_db_name = 'databases/report.json'
        self.personal_key = None
        self.window_name = None

        self.changes_lock = asyncio.Lock()

        # create db's if not exists
        if not path.isdir(self.modules_db_name.split('/')[0]):
            mkdir(self.modules_db_name.split('/')[0])

        for db_params in [
            {"name": self.modules_db_name, "value": "[]"},
            {"name": self.report_db_name, "value": "{}"},
        ]:
            if not path.isfile(db_params["name"]):
                with open(db_params["name"], 'w') as f: f.write(db_params["value"])

        with open('input_data/proxies.txt') as f:
            self.proxies = [
                "http://" + proxy.removeprefix("https://").removeprefix("http://")
                for proxy in f.read().splitlines()
                if proxy not in ['https://log:pass@ip:port', 'http://log:pass@ip:port', 'log:pass@ip:port', '', None]
            ]

        amounts = self.get_amounts()
        logger.info(f'Loaded {amounts["modules_amount"]} modules for {amounts["accs_amount"]} accounts\n')


    def set_password(self):
        if self.personal_key is not None: return

        logger.debug(f'Enter password to encrypt privatekeys (empty for default):')
        raw_password = input("")

        if not raw_password:
            raw_password = "@karamelniy dumb shit encrypting"
            logger.success(f'[+] Soft | You set empty password for Database\n')
        else:
            print(f'')
        sleep(0.2)

        password = md5(raw_password.encode()).hexdigest().encode()
        self.personal_key = Fernet(urlsafe_b64encode(password))


    def get_password(self):
        if self.personal_key is not None: return

        with open(self.modules_db_name, encoding="utf-8") as f: modules_db = json.load(f)
        if not modules_db: return

        first_pk = list(modules_db.keys())[0]
        if not first_pk: return
        try:
            temp_key = Fernet(urlsafe_b64encode(md5("@karamelniy dumb shit encrypting".encode()).hexdigest().encode()))
            self.decode_pk(pk=first_pk, key=temp_key)
            self.personal_key = temp_key
            return
        except InvalidToken: pass

        while True:
            try:
                logger.debug(f'Enter password to decrypt your privatekeys (empty for default):')
                raw_password = input("")
                password = md5(raw_password.encode()).hexdigest().encode()

                temp_key = Fernet(urlsafe_b64encode(password))
                self.decode_pk(pk=first_pk, key=temp_key)
                self.personal_key = temp_key
                logger.success(f'[+] Soft | Access granted!\n')
                return

            except InvalidToken:
                logger.error(f'[-] Soft | Invalid password\n')


    def encode_pk(self, pk: str, key: None | Fernet = None):
        if key is None:
            return self.personal_key.encrypt(pk.encode()).decode()
        return key.encrypt(pk.encode()).decode()


    def decode_pk(self, pk: str, key: None | Fernet = None):
        if key is None:
            return self.personal_key.decrypt(pk).decode()
        return key.decrypt(pk).decode()


    def create_modules(self, mode: int):
        self.set_password()

        with open('input_data/privatekeys.txt') as f:
            privatekeys = f.read().splitlines()
        with open('input_data/recipients.txt') as f:
            recipients = f.read().splitlines()

        if (AFTER_CLAIM["send_token"] or AFTER_CLAIM["send_eth"]) and len(recipients) == 0:
            raise DataBaseError(f"You must provide Recipients to send tokens to exchange")
        elif len(recipients) not in [len(privatekeys), 0]:
            raise DataBaseError(f"Amount of Recipients ({len(recipients)}) must be same as Privatekeys amount ({len(privatekeys)}) or 0")
        if len(recipients) == 0:
            recipients = [None for _ in range(len(privatekeys))]

        with open('input_data/proxies.txt') as f:
            proxies = f.read().splitlines()
        if len(proxies) == 0 or proxies == [""] or proxies == ["http://login:password@ip:port"]:
            logger.error('You will not use proxy')
            proxies = [None for _ in range(len(privatekeys))]
        else:
            proxies = list(proxies * (len(privatekeys) // len(proxies) + 1))[:len(privatekeys)]

        with open(self.report_db_name, 'w') as f: f.write('{}')  # clear report db

        new_modules = {
            self.encode_pk(pk): {
                "address": utils.get_address(pk),
                "modules": [{"module_name": "claim", "status": "to_run"}],
                "recipient": recipient,
                "proxy": proxy,
            }
            for pk, recipient, proxy in zip(privatekeys, recipients, proxies)
        }

        with open(self.modules_db_name, 'w', encoding="utf-8") as f: json.dump(new_modules, f)
        amounts = self.get_amounts()
        logger.info(f'Created Database for {amounts["accs_amount"]} accounts!\n')


    def get_amounts(self):
        with open(self.modules_db_name, encoding="utf-8") as f: modules_db = json.load(f)
        modules_len = sum([len(modules_db[acc]["modules"]) for acc in modules_db])

        for acc in modules_db:
            for index, module in enumerate(modules_db[acc]["modules"]):
                if module["status"] in ["failed", "cloudflare"]: modules_db[acc]["modules"][index]["status"] = "to_run"

        with open(self.modules_db_name, 'w', encoding="utf-8") as f:
            json.dump(modules_db, f)

        if self.window_name == None: self.window_name = WindowName(accs_amount=len(modules_db))
        else: self.window_name.accs_amount = len(modules_db)
        self.window_name.set_modules(modules_amount=modules_len)

        return {
            'accs_amount': len(modules_db),
            'modules_amount': modules_len,
        }


    def get_all_modules(self):
        self.get_password()
        with open(self.modules_db_name, encoding="utf-8") as f: modules_db = json.load(f)

        if not modules_db:
            return 'No more accounts left'

        all_wallets_modules = [
            {
                'privatekey': self.decode_pk(pk=encoded_privatekey),
                'encoded_privatekey': encoded_privatekey,
                'proxy': wallet_data.get("proxy"),
                'recipient': wallet_data.get("recipient"),
                'address': wallet_data["address"],
                'module_info': wallet_data["modules"][0],
                'last': True
            }
            for encoded_privatekey, wallet_data in modules_db.items()
        ]
        if SHUFFLE_WALLETS:
            shuffle(all_wallets_modules)
        return all_wallets_modules


    async def remove_account(self, module_data: dict):
        async with self.changes_lock:
            with open(self.modules_db_name, encoding="utf-8") as f: modules_db = json.load(f)
            self.window_name.add_acc()
            if module_data["module_info"]["status"] in [True, "completed"]:
                del modules_db[module_data["encoded_privatekey"]]
            else:
                modules_db[module_data["encoded_privatekey"]]["modules"] = [
                    {**module, "status": "failed"}
                    for module in modules_db[module_data["encoded_privatekey"]]["modules"]
                ]

            with open(self.modules_db_name, 'w', encoding="utf-8") as f:
                json.dump(modules_db, f)


    async def append_report(self, encoded_pk: str, text: str, success: bool = None):
        async with self.changes_lock:
            status_smiles = {True: '✅ ', False: "❌ ", None: ""}

            with open(self.report_db_name, encoding="utf-8") as f: report_db = json.load(f)

            if not report_db.get(encoded_pk): report_db[encoded_pk] = {'texts': [], 'success_rate': [0, 0]}

            report_db[encoded_pk]["texts"].append(status_smiles[success] + text)
            if success != None:
                report_db[encoded_pk]["success_rate"][1] += 1
                if success == True: report_db[encoded_pk]["success_rate"][0] += 1

            with open(self.report_db_name, 'w') as f: json.dump(report_db, f)


    async def get_account_reports(self, encoded_pk: str, get_rate: bool = False):
        async with self.changes_lock:
            with open(self.report_db_name, encoding="utf-8") as f: report_db = json.load(f)

            decoded_privatekey = self.decode_pk(pk=encoded_pk)
            account_index = f"[{self.window_name.accs_done}/{self.window_name.accs_amount}]"
            required_string = f'\n‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾\n{utils.get_ad_tg()}'

            if report_db.get(encoded_pk):
                account_reports = report_db[encoded_pk]
                if get_rate: return f'{account_reports["success_rate"][0]}/{account_reports["success_rate"][1]}'
                del report_db[encoded_pk]

                with open(self.report_db_name, 'w', encoding="utf-8") as f: json.dump(report_db, f)

                logs_text = '\n'.join(account_reports['texts'])
                tg_text = f'{account_index} <b>{utils.get_address(pk=decoded_privatekey)}</b>\n\n{logs_text}'
                if account_reports["success_rate"][1]:
                    tg_text += f'\n\nSuccess rate {account_reports["success_rate"][0]}/{account_reports["success_rate"][1]}{required_string}'

                return tg_text

            else:
                return f'{account_index} <b>{utils.get_address(pk=decoded_privatekey)}</b>\n\nNo actions{required_string}'
