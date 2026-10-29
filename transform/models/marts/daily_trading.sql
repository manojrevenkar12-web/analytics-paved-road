-- Trading activity per day, country, asset class and channel (contract: config/products/daily_trading.yaml)
select
    t.trade_date,
    c.country,
    i.asset_class,
    t.channel,
    cast(count(*) as bigint) as trades,
    cast(count(distinct t.customer_id) as bigint) as active_traders,
    cast(sum(case when t.side = 'buy' then t.amount_eur else 0 end) as double) as buy_volume_eur,
    cast(sum(case when t.side = 'sell' then t.amount_eur else 0 end) as double) as sell_volume_eur,
    cast(sum(t.fee_eur) as double) as fees_eur
from {{ ref('stg_trades') }} t
join {{ source('raw', 'customers') }} c on c.customer_id = t.customer_id
join {{ source('raw', 'instruments') }} i on i.isin = t.isin
group by 1, 2, 3, 4
