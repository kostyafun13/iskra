#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ВЫГРУЗКА ТЕЛЕГРАМА — своя переписка как база связей.

Что делает: проходит по личным диалогам, забирает последние сообщения каждого,
считает механику (кто сколько писал, голосовые, звонки, кто написал последним)
и складывает всё на диск. Второй запуск дочитывает только новое.

Всё локально: файлы остаются на вашем компьютере, никуда не отправляются.

Запуск:
    pip3 install telethon
    python3 vygruzka_tg.py --dney 180 --min-soobscheniy 10
    python3 vygruzka_tg.py --dozor            # только показать план

Ключи берутся из файла .env рядом со скриптом:
    TG_API_ID=1234567
    TG_API_HASH=...
Получить их: my.telegram.org → API development tools.
"""
import argparse, json, os, sys, time
import datetime as dt

try:
    from telethon.sync import TelegramClient
    from telethon.tl.types import (
        User, MessageService, MessageActionPhoneCall, DocumentAttributeAudio,
    )
    from telethon.errors import FloodWaitError
except ImportError:
    sys.exit("Нет Telethon. Поставьте: pip3 install telethon")

HERE = os.path.dirname(os.path.abspath(__file__))
DIALOGI = os.path.join(HERE, "dialogi")
REESTR = os.path.join(HERE, "reestr.json")
SESSIYA = os.path.join(HERE, "tg_sessiya")
MSK = dt.timezone(dt.timedelta(hours=3))


def klyuchi():
    """api_id и api_hash из .env рядом со скриптом или из окружения."""
    put = os.path.join(HERE, ".env")
    if os.path.exists(put):
        for stroka in open(put, encoding="utf-8"):
            stroka = stroka.strip()
            if not stroka or stroka.startswith("#") or "=" not in stroka:
                continue
            k, _, v = stroka.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    api_id, api_hash = os.environ.get("TG_API_ID"), os.environ.get("TG_API_HASH")
    if not api_id or not api_hash:
        sys.exit("Нет TG_API_ID / TG_API_HASH. Положите их в .env рядом со скриптом.")
    return int(api_id), api_hash


def chitat_reestr():
    if os.path.exists(REESTR):
        try:
            return json.load(open(REESTR, encoding="utf-8"))
        except Exception:
            pass
    return {}


def imya(ent):
    chasti = [getattr(ent, "first_name", "") or "", getattr(ent, "last_name", "") or ""]
    return " ".join(c for c in chasti if c).strip() or "без имени"


def golos_minut(m):
    for a in (getattr(m, "document", None).attributes if getattr(m, "document", None) else []):
        if isinstance(a, DocumentAttributeAudio) and getattr(a, "voice", False):
            return max(1, int(getattr(a, "duration", 0) or 0) // 60)
    return 0


def vygruzit(client, ent, skolko, bylo_max_id):
    """Забирает сообщения диалога новее bylo_max_id. Возвращает список и метрики."""
    stroki, met = [], dict(vsego=0, moi=0, ego=0, golos=0, golos_min=0, zvonki=0,
                          simvolov=0, max_id=bylo_max_id, posledniy="", data="")
    posledniy_out = None
    try:
        for m in client.iter_messages(ent, limit=skolko):
            if bylo_max_id and m.id <= bylo_max_id:
                break
            met["max_id"] = max(met["max_id"], m.id)
            if isinstance(m, MessageService):
                if isinstance(getattr(m, "action", None), MessageActionPhoneCall):
                    met["zvonki"] += 1
                continue
            gm = golos_minut(m)
            if gm:
                met["golos"] += 1
                met["golos_min"] += gm
            tekst = (m.message or "").strip()
            if not tekst and not m.media:
                continue
            met["vsego"] += 1
            met["simvolov"] += len(tekst)
            if m.out:
                met["moi"] += 1
            else:
                met["ego"] += 1
            if posledniy_out is None:
                posledniy_out = bool(m.out)
                met["data"] = m.date.astimezone(MSK).strftime("%Y-%m-%d %H:%M")
            stroki.append({
                "id": m.id,
                "d": m.date.astimezone(MSK).strftime("%Y-%m-%d %H:%M"),
                "ya": 1 if m.out else 0,
                "t": tekst or "[медиа]",
            })
    except FloodWaitError as e:
        print(f"   Телеграм просит подождать {e.seconds} с — ждём, это не бан")
        time.sleep(e.seconds + 2)
    met["posledniy"] = "я" if posledniy_out else "он"
    return list(reversed(stroki)), met


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dney", type=int, default=180, help="брать диалоги с активностью за N дней")
    ap.add_argument("--min-soobscheniy", dest="min_soobscheniy", type=int, default=10,
                    help="порог содержательности диалога")
    ap.add_argument("--soobscheniy", type=int, default=60, help="сколько последних сообщений брать")
    ap.add_argument("--dozor", action="store_true", help="только показать план, ничего не качать")
    a = ap.parse_args()

    api_id, api_hash = klyuchi()
    os.makedirs(DIALOGI, exist_ok=True)
    reestr = chitat_reestr()
    granica = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=a.dney)

    client = TelegramClient(SESSIYA, api_id, api_hash)
    with client:
        plan, staryh_podryad = [], 0
        for d in client.iter_dialogs():
            if d.date and d.date < granica:
                staryh_podryad += 1
                if staryh_podryad > 100:      # лента идёт по убыванию даты — дальше только старьё
                    break                     # без этого большая книжка перебирается целиком, минуты
                continue
            staryh_podryad = 0
            ent = d.entity
            if not isinstance(ent, User) or getattr(ent, "bot", False) or getattr(ent, "deleted", False):
                continue                      # только живые люди: боты, группы и каналы мимо
            plan.append((str(ent.id), ent, d))

        print(f"Диалогов под условие: {len(plan)} (активность за {a.dney} дн.)")
        if a.dozor:
            for uid, ent, d in plan[:40]:
                print(f"  · {imya(ent)} (@{getattr(ent,'username','') or '—'})")
            print("режим дозора — ничего не скачано")
            return

        novyh, dopisano, propuscheno = 0, 0, 0
        for n, (uid, ent, d) in enumerate(plan, 1):
            staroe = reestr.get(uid, {})
            stroki, met = vygruzit(client, ent, a.soobscheniy, staroe.get("max_id", 0))
            if not stroki and staroe:
                continue                       # нового нет — идём дальше
            if not staroe and met["vsego"] < a.min_soobscheniy:
                propuscheno += 1
                continue
            put = os.path.join(DIALOGI, f"{uid}.jsonl")
            with open(put, "a", encoding="utf-8") as f:
                for s in stroki:
                    f.write(json.dumps(s, ensure_ascii=False) + "\n")
            zapis = staroe or {}
            zapis.update({
                "imya": imya(ent),
                "nik": getattr(ent, "username", "") or "",
                "max_id": met["max_id"],
                "posledniy": met["posledniy"],
                "data": met["data"] or zapis.get("data", ""),
            })
            for k in ("vsego", "moi", "ego", "golos", "golos_min", "zvonki", "simvolov"):
                zapis[k] = zapis.get(k, 0) + met[k]
            reestr[uid] = zapis
            if staroe:
                dopisano += 1
            else:
                novyh += 1
            if n % 25 == 0:
                json.dump(reestr, open(REESTR, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
                print(f"  …{n} из {len(plan)}")
            time.sleep(0.4)                    # неагрессивно, чтобы не ловить FloodWait

        json.dump(reestr, open(REESTR, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        zhdut = sum(1 for v in reestr.values() if v.get("posledniy") == "он")
        print(f"\nГотово. Новых диалогов {novyh}, дописано {dopisano}, "
              f"пропущено как короткие {propuscheno}.")
        print(f"Всего в базе: {len(reestr)}. Последнее слово за собеседником: {zhdut}.")
        print(f"Файлы: {DIALOGI}/  ·  реестр: {REESTR}")


if __name__ == "__main__":
    main()
