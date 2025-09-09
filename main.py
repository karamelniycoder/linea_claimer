from random import randint
from loguru import logger
from time import sleep
import warnings
import asyncio
import os

from modules import *
from modules.retry import DataBaseError
from modules import utils
from settings import THREADS, SLEEP_AFTER_ACCOUNT


async def run_modules(
        mode: int,
        module_data: dict,
        sem: asyncio.Semaphore,
):
    async with sem:
        try:
            browser = Browser(
                proxy=module_data["proxy"],
                encoded_pk=module_data["encoded_privatekey"],
                address=module_data["address"],
                db=db,
            )
            wallet = Wallet(
                privatekey=module_data["privatekey"],
                encoded_pk=module_data["encoded_privatekey"],
                recipient=module_data["recipient"],
                db=db,
            )
            module_data["module_info"]["status"] = await Linea(wallet=wallet, browser=browser).run()

        except DataBaseError:
            module_data = None
            raise

        except Exception as err:
            logger.error(f'[-] Soft | {wallet.address} | Global error: {err}')
            await db.append_report(encoded_pk=module_data["encoded_privatekey"], text=str(err), success=False)

        finally:
            if type(module_data) == dict:
                await browser.close_sessions()
                await db.remove_account(module_data)

                reports = await db.get_account_reports(encoded_pk=module_data["encoded_privatekey"])
                await TgReport().send_log(logs=reports)

                await utils.async_sleep(randint(*SLEEP_AFTER_ACCOUNT))


async def runner(mode: int):
    all_modules = db.get_all_modules()
    sem = asyncio.Semaphore(THREADS)
    RPCInitializer(proxies=db.proxies)

    if all_modules != 'No more accounts left':
        await asyncio.gather(*[
            run_modules(
                mode=mode,
                module_data=module_data,
                sem=sem,
            )
            for module_data in all_modules
        ])

    logger.success(f'All accounts done.')
    return 'Ended'


if __name__ == '__main__':
    warnings.filterwarnings("ignore")

    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    try:
        db = DataBase()

        while True:
            mode = choose_mode()

            match mode.type:
                case "database":
                    db.create_modules(mode=mode.soft_id)

                case "module":
                    if asyncio.run(runner(mode=mode.soft_id)) == "Ended": break
                    print('')


        utils.ad()
        sleep(0.1)
        input('\n > Exit\n')

    except DataBaseError as e:
        logger.error(f'[-] Database | {e}')

    except KeyboardInterrupt:
        pass

    finally:
        logger.info('[•] Soft | Closed')



