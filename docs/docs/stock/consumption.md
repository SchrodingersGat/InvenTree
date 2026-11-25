---
title: Stock Consumption
---

## Stock Consumption

A stock item is *consumed* when it leaves inventory into another object. Consumed stock is no longer [available](./availability.md), and it has no stock location, but it is retained in the database so that its history remains traceable.

Stock can be consumed by the following objects:

| Consumer | When | Audit event |
| --- | --- | --- |
| [Build Order](../manufacturing/build.md) | Allocated stock is consumed by the build order | Consumed by build order |
| [Sales Order](../sales/sales_order.md) | Allocated stock is shipped against the sales order | Shipped against Sales Order |
| Stock Item | Stock is installed into another stock item (e.g. a tracked component installed into a build output) | Installed into assembly |
| [Customer](../sales/customer.md) | Stock is assigned directly to a customer | Sent to customer |

Each stock item records a single reference to the object which has consumed it. When stock is consumed:

- If only part of the stock item is consumed, the consumed quantity is split off into a new stock item, and the remainder stays in stock
- The consumed stock item is removed from its location
- An entry is recorded in the [stock tracking](./tracking.md) history of the item (and, for an installed item, of the item it is installed into)

### Customer

The *Customer* of a stock item is derived from its consumer:

- Stock shipped against a sales order belongs to the customer of that sales order
- Stock assigned directly to a customer belongs to that customer

Only the *direct* consumer of a stock item is considered. For example, a component installed into an assembly which has been shipped to a customer does not itself have a customer.

### Build Orders

The *Consumed Stock* tab of a build order shows the stock consumed by the build order, *including* any tracked stock which was installed into the outputs of the build order.

Tracked stock which is installed into a build output is consumed by the output (rather than the build order itself). The build order is recorded in the stock tracking history of the installed item.

## Returning Consumed Stock

Consumed stock can be returned into stock, which removes the consumption reference and places the item into the selected stock location:

- Stock consumed by a build order, or assigned to a customer, can be returned via the *Return* stock action
- Installed stock items can be *uninstalled* from the item they are installed in
- Stock shipped to a customer can be received against a [Return Order](../sales/return_order.md)

If only part of a consumed stock item is returned, the returned quantity is split off into a new stock item, and the remainder stays consumed.

## Deleting a Consumer

When an object which has consumed stock is deleted:

| Consumer | Consumed stock |
| --- | --- |
| Build Order | Kept - the stock remains consumed |
| Sales Order | Kept - the stock remains consumed |
| Customer | Kept - the stock remains consumed |
| Stock Item | Deleted - stock items installed in a deleted stock item are also deleted |

Stock which remains consumed after its consumer is deleted is not returned to inventory. The consumer is shown as *deleted* (with its type and ID), and the original consumer remains visible in the stock tracking history. Such stock can still be returned to stock.

!!! info "Customer of a deleted sales order"
    As the customer is derived from the consumer, stock shipped against a sales order which has since been deleted no longer reports a customer.

## API

The consumer of a stock item is provided by the following fields of the stock item API:

| Field | Description |
| --- | --- |
| `consumed_by_model_type` | The type of the consuming object (`build`, `salesorder`, `stockitem` or `company`), or `null` |
| `consumed_by_model_id` | The ID of the consuming object, or `null` |
| `consumed_by_detail` | Optional detail of the consuming object (`model_type`, `pk`, `name`, `url`, `deleted`) |
| `customer` | The ID of the customer (derived from the consumer), or `null` |

These fields are read-only. Stock is consumed via the relevant actions (e.g. completing a build order, shipping a sales order, installing a stock item, or assigning stock to a customer).

The stock item list can be filtered by consumer, using the `consumed`, `consumed_by_model_type`, `consumed_by_model_id`, `build_consumed`, `installed_in` and `customer` filters.

### Plugin Events

When stock is consumed, the `stockitem.consumed` [event](../plugins/mixins/event.md) is triggered for each consumed stock item, with the following keyword arguments:

| Argument | Description |
| --- | --- |
| `id` | The ID of the consumed stock item |
| `quantity` | The quantity consumed |
| `consumer_model_type` | The type of the consuming object (e.g. `build`) |
| `consumer_id` | The ID of the consuming object |
