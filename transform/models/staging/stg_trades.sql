select
    trade_id,
    customer_id,
    isin,
    executed_at,
    cast(executed_at as date) as trade_date,
    side,
    amount_eur,
    coalesce(fee_eur, 0) as fee_eur,
    channel
from {{ source('raw', 'trades') }}
