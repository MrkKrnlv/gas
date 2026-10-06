"""Демо-данные: 3 клиента с газгольдерами и историей заправок (даты считаются от сегодняшнего дня).
Запуск: python seed.py   (повторно не добавит, если демо уже есть)"""
from datetime import date, timedelta

import main

TODAY = date.today()
ago = lambda n: (TODAY - timedelta(days=n)).isoformat()
ahead = lambda n: (TODAY + timedelta(days=n)).isoformat()


def add(con, table, **kw):
    cols = list(kw)
    cur = con.execute(f"INSERT INTO {table}({','.join(cols)}) VALUES({','.join('?' * len(cols))})", list(kw.values()))
    return cur.lastrowid


def refill(con, client, tank, referrer, **kw):
    d = main.derive_refill(con, main.Refill(client_id=client, tank_id=tank, referrer_id=referrer, **kw).model_dump())
    cols = main.FIELDS["refills"]
    con.execute(f"INSERT INTO refills({','.join(cols)}) VALUES({','.join('?' * len(cols))})", [d.get(k) for k in cols])


def history(con, client, tank, referrer, last_ago, step, vols, prices, cost, pay, first_reward=0, unpaid_last=False):
    """Заправки от старых к новым: последняя last_ago дней назад, дальше через step дней."""
    n = len(vols)
    for i in range(n):
        last = i == n - 1
        refill(con, client, tank, referrer, status="done", done_date=ago(last_ago + (n - 1 - i) * step),
               volume_l=vols[i], price_per_liter=prices[i], cost_per_liter=cost, payment_method=pay,
               paid=not (last and unpaid_last), referrer_reward=first_reward if i == 0 else 0)


with main.db() as con:
    if con.execute("SELECT 1 FROM clients WHERE name='Алексей Громов'").fetchone():
        raise SystemExit("Демо-данные уже добавлены")
    dm = add(con, "referrers", name="Дмитрий (монтажник)", phone="+7 905 111-22-33", reward_default=1000, note="Ставит газгольдеры, приводит новых клиентов")
    sv = add(con, "referrers", name="Сергей Волков", phone="+7 921 444-55-66", reward_default=500, note="Сосед, рекомендует знакомым")

    # 1. Скоро пора заправляться
    c1 = add(con, "clients", name="Алексей Громов", phone="+7 921 555-01-12", telegram="@gromov_a", source="Рекомендация",
             address="Всеволожск, ул. Лесная, 14", referrer_id=dm, note="Дом 150 м2, отопление только газом. Звонить после 18:00.")
    t1 = add(con, "tanks", client_id=c1, title="Дом", volume_l=4850, brand="Газсервис", serial="GS-4821", interval_days=45,
             installed_at="2023-06-15", inspection_date=ahead(52))
    history(con, c1, t1, dm, last_ago=38, step=45, vols=[3200, 3050, 3300, 3100], prices=[27.5, 28.5, 30, 31],
            cost=22.5, pay="transfer", first_reward=1000)

    # 2. Просрочена заправка, последняя не оплачена
    c2 = add(con, "clients", name="Ирина Белова", phone="+7 911 222-33-44", source="Яндекс Директ",
             address="Кудрово, ул. Мира, 3", note="Баня и дом на одном баллоне. Просила напоминать за неделю.")
    t2 = add(con, "tanks", client_id=c2, title="Дом + баня", volume_l=2700, brand="Gas-Tank", serial="GT-77120", interval_days=60,
             installed_at="2024-09-02", inspection_date=ahead(210))
    history(con, c2, t2, None, last_ago=75, step=60, vols=[1800, 1650, 1900], prices=[29, 30.5, 31.5],
            cost=23, pay="cash", unpaid_last=True)

    # 3. Два газгольдера, заявка в работе, отменённая
    c3 = add(con, "clients", name="Павел Соколов", phone="+7 903 777-88-99", telegram="@sokolov_pv", source="Рекомендация",
             address="Токсово, Лесной пр., 8", referrer_id=sv, note="Заезд через шлагбаум, код 1974. Оплата переводом.")
    t3a = add(con, "tanks", client_id=c3, title="Основной", volume_l=4850, brand="Газсервис", serial="GS-5530", interval_days=30,
              installed_at="2022-10-20", inspection_date=ahead(20))
    t3b = add(con, "tanks", client_id=c3, title="Гараж и мастерская", volume_l=2000, brand="Gas-Tank", interval_days=90,
              installed_at="2024-02-11")
    history(con, c3, t3a, sv, last_ago=12, step=30, vols=[3500, 3400, 3650, 3300, 3450], prices=[27, 28, 29.5, 30.5, 32],
            cost=23.5, pay="transfer", first_reward=500)
    history(con, c3, t3b, sv, last_ago=40, step=90, vols=[1500, 1400], prices=[28.5, 31], cost=23, pay="card")
    refill(con, c3, t3a, sv, status="scheduled", scheduled_date=ahead(3), volume_l=3500, price_per_liter=32,
           note="Просил привезти в субботу до обеда")
    refill(con, c3, t3b, sv, status="cancelled", scheduled_date=ago(25), note="Передумал, заправился у других")
print("Готово: добавлено 3 клиента, 4 газгольдера, 2 привёдших и история заправок")
