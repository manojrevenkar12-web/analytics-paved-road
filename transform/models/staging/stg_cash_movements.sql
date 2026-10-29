select
    movement_id,
    customer_id,
    booked_at,
    cast(date_trunc('month', booked_at) as date) as month,
    type,
    amount_eur
from {{ source('raw', 'cash_movements') }}
