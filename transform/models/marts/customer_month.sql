-- One row per customer and month with flows and activity (contract: config/products/customer_month.yaml)
with flows as (
    select
        customer_id,
        month,
        sum(case when type = 'deposit' then amount_eur else 0 end) as deposits_eur,
        sum(case when type = 'withdrawal' then -amount_eur else 0 end) as withdrawals_eur
    from {{ ref('stg_cash_movements') }}
    group by 1, 2
),
activity as (
    select
        customer_id,
        cast(date_trunc('month', trade_date) as date) as month,
        count(*) as trades,
        sum(case when channel = 'savings_plan' then 1 else 0 end) as savings_plan_executions
    from {{ ref('stg_trades') }}
    group by 1, 2
),
keys as (
    select customer_id, month from flows
    union
    select customer_id, month from activity
)
select
    k.month,
    k.customer_id,
    c.country,
    c.segment,
    cast(coalesce(f.deposits_eur, 0) as double) as deposits_eur,
    cast(coalesce(f.withdrawals_eur, 0) as double) as withdrawals_eur,
    cast(coalesce(f.deposits_eur, 0) - coalesce(f.withdrawals_eur, 0) as double) as net_inflow_eur,
    cast(coalesce(a.trades, 0) as bigint) as trades,
    cast(coalesce(a.savings_plan_executions, 0) as bigint) as savings_plan_executions
from keys k
join {{ source('raw', 'customers') }} c on c.customer_id = k.customer_id
left join flows f on f.customer_id = k.customer_id and f.month = k.month
left join activity a on a.customer_id = k.customer_id and a.month = k.month
