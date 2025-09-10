
SHUFFLE_WALLETS     = True                              # True | False - перемешивать ли кошельки
RETRY               = 3

ETH_MAX_GWEI        = 25
GWEI_MULTIPLIER     = 1.5                               # умножать текущий гвей при отправке транз на 50%
TO_WAIT_TX          = 1                                 # сколько минут ожидать транзакцию. если транза будет находится в пендинге после указанного времени то будет считатся зафейленной

RPCS                = {
    'ethereum'      : [
        "https://rpc.flashbots.net/fast",
        "https://eth.rpc.blxrbdn.com",
        "https://1rpc.io/eth",
        "https://eth.drpc.org",
        "https://ethereum-rpc.publicnode.com",
    ],
    'linea'      : [
        "https://1rpc.io/linea",
        "https://rpc.linea.build",
        "https://linea.therpc.io",
        "https://linea-rpc.publicnode.com",
    ],
}

# --- CLAIM SETTINGS ---
AFTER_CLAIM         = {
    "swap"          : False,                            # после клейма $LINEA - свапать его в ETH на Odos
    "slippage"      : 5,                                # проскальзывание для свапа на ODOS

    "send_token"    : False,                            # после клейма $LINEA - отправлять его на биржу

    "send_eth"      : False,                            # после всех действий - отправлять ETH на биржу
    "keep_eth"      : [0.0003, 0.0008],                 # сколько оставлять ETH на кошельке при отправке на биржу
}


SLEEP_AFTER_TX      = [5, 10]                           # задержка после каждой транзакции
SLEEP_AFTER_ACCOUNT = [30, 60]                          # задержка после каждого аккаунта

# --- GENERAL SETTINGS ---
THREADS             = 1                                 # количество потоков (одновременно работающих кошельков)


# --- PERSONAL SETTINGS ---
TG_BOT_TOKEN        = ''                                # токен от тг бота (`12345:Abcde`) для уведомлений. если не нужно - оставляй пустым
TG_USER_ID          = []                                # тг айди куда должны приходить уведомления.
                                                        # [21957123] - для отправления уведомления только себе
                                                        # [21957123, 103514123] - отправлять нескольким людями
