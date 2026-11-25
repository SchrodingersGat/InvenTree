---
title: Stock Consumption
---

## Stock Consumption

A stock item is *consumed* when it leaves inventory into another object - for example, when it is used by a build order, or shipped to a customer. Consumed stock is no longer [available](./availability.md) and has no stock location, but it is retained in the database, so that its history remains [traceable](./traceability.md).

Each consumed stock item records a single reference to the object which consumed it (its *consumer*).

!!! info "Consumed vs Depleted"
    Consuming stock is different to *depleting* (removing) stock. When stock is removed, its quantity is reduced, without recording what it was used for. When stock is consumed, it is kept as a separate stock item, linked to its consumer.

## Consuming Stock

Stock is consumed by the following actions:

| Consumer | Action | Stock tracking event |
| --- | --- | --- |
| [Build Order](../manufacturing/build.md) | Allocated stock is [consumed](../manufacturing/allocate.md#consuming-stock) by the build order (manually, or when the build order is completed) | Consumed by build order |
| Stock Item | A [tracked](../manufacturing/allocate.md#tracked-stock) component is installed into a build output when the output is completed, or a stock item is [installed](./tracking.md#installed-stock-items) into another stock item using the *Install Item* action | Installed into assembly |
| [Sales Order](../sales/sales_order.md) | A sales order [shipment](../sales/sales_order.md#complete-shipment) is completed | Shipped against Sales Order |
| [Customer](../sales/customer.md) | Stock is assigned directly to a customer, using the *Assign Stock* action | Sent to customer |

When stock is consumed:

- If only part of a stock item is consumed, the consumed quantity is split off into a new stock item, and the remainder stays in stock
- The consumed stock item is removed from its stock location
- An entry is recorded in the [stock tracking](./tracking.md) history of the consumed item - and, for an installed item, of the item it is installed into

### Build Orders

Untracked stock is consumed by the build order itself. Tracked stock is installed into (and so consumed by) the build output it was allocated to, and the build order is recorded in the stock tracking history of the installed item.

The *Consumed Stock* tab of a build order shows both: the stock consumed by the build order, and the stock installed into its outputs.

### Customer

The *Customer* of a stock item is derived from its consumer:

- Stock shipped against a sales order belongs to the customer of that sales order
- Stock assigned directly to a customer belongs to that customer

Only the *direct* consumer of a stock item is considered. For example, a component installed into an assembly which has been shipped to a customer does not itself have a customer.

The *Assigned Stock* tab of a [customer](../sales/customer.md) shows all of the stock sent to that customer, by either method.

## Viewing Consumed Stock

Consumed stock can be found in the following places:

- The *Consumed* badge on the stock item detail page - hover over the badge to see what consumed the item
- The *Consumed By* field in the stock item details, which links to the consumer
- The *Consumed By* column of stock item tables - this column is hidden by default, and can be enabled using the column selector
- The *Consumed Stock* tab of a build order, the *Installed Items* tab of a stock item, and the *Assigned Stock* tab of a customer

Stock item tables display only *in stock* items by default. To display consumed stock, remove the *In Stock* filter, or use the *Consumed* or *Consumer Type* filters.

## Restrictions

While a stock item is consumed, it cannot be merged with other stock, serialized, or allocated to an order. It also cannot be moved or counted, unless the {{ globalsetting("STOCK_ALLOW_OUT_OF_STOCK_TRANSFER", short=True) }} setting is enabled.

To perform any of these actions, the stock item must first be [returned to stock](#returning-consumed-stock).

## Returning Consumed Stock

Consumed stock can be returned into stock, which removes the reference to its consumer, and places the item into the selected stock location:

| Consumer | How to return the stock |
| --- | --- |
| Build Order | Use *Return to Stock* in the *Consumed Stock* tab of the build order, or the *Return Stock* action on the stock item |
| Stock Item | Use *Uninstall Item* in the *Installed Items* tab of the parent item. Installed items are also uninstalled when the parent item is [disassembled](./disassemble.md) |
| Sales Order | Receive the stock item against a [Return Order](../sales/return_order.md#receive-line-items), or use the *Return Stock* action on the stock item |
| Customer | Receive the stock item against a [Return Order](../sales/return_order.md#receive-line-items), or use the *Return Stock* action on the stock item |

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

The consumer of a stock item is provided by the following (read-only) fields of the stock item API:

| Field | Description |
| --- | --- |
| `consumed_by_model_type` | The type of the consuming object (`build`, `salesorder`, `stockitem` or `company`), or `null` |
| `consumed_by_model_id` | The ID of the consuming object, or `null` |
| `consumed_by_detail` | Optional detail of the consuming object (`model_type`, `pk`, `name`, `url`, `deleted`). This is `null` if the user does not have permission to view the consumer |
| `customer` | The ID of the customer (derived from the consumer), or `null` |

Stock cannot be consumed by editing these fields - it is consumed via the actions listed [above](#consuming-stock), each of which has a corresponding API endpoint.

The stock item list can be filtered by consumer:

| Filter | Description |
| --- | --- |
| `consumed` | Stock which has (or has not) been consumed |
| `consumed_by_model_type` | Stock consumed by a particular type of object (e.g. `build`) |
| `consumed_by_model_id` | Stock consumed by an object with the provided ID (use with `consumed_by_model_type`) |
| `build_consumed` | Stock consumed by a build order, including stock installed into its outputs |
| `installed_in` | Stock installed in a particular stock item |
| `customer` | Stock sent to a particular customer (directly, or via a sales order) |

### Plugin Events

When stock is consumed, the `stockitem.consumed` [event](../plugins/mixins/event.md) is triggered for each consumed stock item, with the following keyword arguments:

| Argument | Description |
| --- | --- |
| `id` | The ID of the consumed stock item |
| `quantity` | The quantity consumed |
| `consumer_model_type` | The type of the consuming object (e.g. `build`) |
| `consumer_id` | The ID of the consuming object |
