import type { APIResponse } from '@playwright/test';

import { createApi } from '../api.js';
import { expect, generateUUID, test } from '../baseFixtures.js';
import { clearTableFilters, loadTab, navigate } from '../helpers.js';
import { doCachedLogin } from '../login.js';

for (const savedFilter of [false, true]) {
  test(`Stock - Consumers${savedFilter ? ' with saved filter' : ''}`, async ({
    browser
  }) => {
    const api = await createApi({});
    const stockIds: number[] = [];
    const companyIds: number[] = [];
    let locationId: number | undefined;
    let partId: number | undefined;

    const create = async (url: string, data: object) => {
      const response = await api.post(url, { data });
      expect(response.status(), await response.text()).toBe(201);
      const body = await response.json();
      return Array.isArray(body) ? body[0] : body;
    };

    try {
      const part = await create('part/', {
        name: `Consumed reference ${generateUUID().slice(0, 8)}`,
        assembly: true,
        component: true,
        salable: true
      });
      partId = part.pk;
      const parent = await create('stock/', { part: partId, quantity: 10 });
      stockIds.push(parent.pk);

      // Each consumer is a customer, which the stock is assigned to
      const consumers = [];
      for (let index = 0; index < 2; index++) {
        const customer = await create('company/', {
          name: `Consumer ${index} ${generateUUID().slice(0, 8)}`,
          description: 'Consumer',
          is_customer: true
        });
        consumers.push({ ...customer, reference: customer.name });
        companyIds.push(customer.pk);
      }

      const location = await create('stock/location/', {
        name: `Reference location ${generateUUID().slice(0, 8)}`
      });
      locationId = location.pk;

      // Partial transfers create children; the parent field is read-only.
      const children = [];
      for (const consumer of [...consumers, null]) {
        const response = await api.post('stock/transfer/', {
          data: {
            location: locationId,
            items: [{ pk: parent.pk, quantity: 1 }]
          }
        });
        expect(response.ok(), await response.text()).toBeTruthy();
        const records = await (
          await api.get('stock/', { params: { parent: parent.pk } })
        ).json();
        const child = records.find(
          (record: { pk: number }) => !stockIds.includes(record.pk)
        );
        expect(child).toBeTruthy();
        stockIds.push(child.pk);
        const patch = await api.patch(`stock/${child.pk}/`, {
          data: { batch: `Reference-${generateUUID().slice(0, 8)}` }
        });
        expect(patch.ok(), await patch.text()).toBeTruthy();

        // Consume the stock item, by assigning it to the customer
        if (consumer) {
          const assign = await api.post('stock/assign/', {
            data: { items: [{ item: child.pk }], customer: consumer.pk }
          });
          expect(assign.ok(), await assign.text()).toBeTruthy();
        }

        children.push(await patch.json());
      }

      const unconsumedChild = children[consumers.length];
      const page = await doCachedLogin(browser);
      const childRequests: URL[] = [];
      page.on('request', (request) => {
        const url = new URL(request.url());
        if (
          request.method() === 'GET' &&
          url.pathname === '/api/stock/' &&
          url.searchParams.get('parent') === String(parent.pk)
        ) {
          childRequests.push(url);
        }
      });

      // Cover fresh table preferences and a previously saved in-stock filter.
      await page.evaluate((saved) => {
        localStorage.removeItem('inventree-filterset-table-child-stock');
        if (saved)
          localStorage.setItem(
            'inventree-filterset-table-child-stock',
            JSON.stringify([{ name: 'in_stock', value: 'true' }])
          );
      }, savedFilter);
      await navigate(page, `stock/item/${parent.pk}/child-items`);
      await loadTab(page, 'Child Items');
      const header = page.getByRole('columnheader', {
        name: 'Consumed By',
        exact: true
      });
      await expect(header).toBeVisible();

      if (savedFilter) {
        await expect(
          page.getByRole('cell', { name: unconsumedChild.batch, exact: true })
        ).toBeVisible();
        await expect(
          page.getByRole('link', { name: consumers[0].reference, exact: true })
        ).toHaveCount(0);
        await page.reload();
        await expect(
          page.getByRole('cell', { name: unconsumedChild.batch, exact: true })
        ).toBeVisible();
        await expect(
          page.getByRole('link', { name: consumers[0].reference, exact: true })
        ).toHaveCount(0);
        await clearTableFilters(page);
      }

      for (const [index, consumer] of consumers.entries()) {
        const row = page.getByRole('row').filter({
          has: page.getByRole('cell', {
            name: children[index].batch,
            exact: true
          })
        });
        await expect(row).toBeVisible();
        await expect(
          row.getByRole('link', { name: consumer.reference, exact: true })
        ).toHaveAttribute('href', `/web/company/${consumer.pk}/`);
      }

      expect(childRequests.length).toBeGreaterThan(0);
      expect(
        childRequests.every(
          (url) => url.searchParams.get('consumed_by_detail') === 'true'
        )
      ).toBeTruthy();

      const unconsumed = page.getByRole('row').filter({
        has: page.getByRole('cell', {
          name: unconsumedChild.batch,
          exact: true
        })
      });
      await expect(unconsumed).toBeVisible();
      const columnIndex = await header.evaluate((element) =>
        Array.from(element.parentElement!.children).indexOf(element)
      );
      await expect(unconsumed.getByRole('cell').nth(columnIndex)).toBeEmpty();

      if (!savedFilter) {
        await page.getByLabel('table-select-columns').click();
        await page
          .getByRole('checkbox', { name: 'Consumed By', exact: true })
          .uncheck();
        await page.keyboard.press('Escape');
        await page.reload();
        await expect(header).toHaveCount(0);
        await page.getByLabel('table-select-columns').click();
        await page
          .getByRole('checkbox', { name: 'Consumed By', exact: true })
          .check();
        await page.keyboard.press('Escape');
        await page
          .getByRole('link', { name: consumers[0].reference, exact: true })
          .click();
        await expect(page).toHaveURL(new RegExp(`/company/${consumers[0].pk}`));
      }
    } finally {
      try {
        const cleanupFailures: string[] = [];
        const cleanup = async (response: APIResponse) => {
          if (!response.ok()) cleanupFailures.push(await response.text());
        };

        for (const pk of stockIds.reverse())
          await cleanup(await api.delete(`stock/${pk}/`));
        for (const pk of companyIds)
          await cleanup(await api.delete(`company/${pk}/`));

        if (partId) {
          await cleanup(
            await api.patch(`part/${partId}/`, { data: { active: false } })
          );
          await cleanup(await api.delete(`part/${partId}/`));
        }

        if (locationId)
          await cleanup(
            await api.delete(`stock/location/${locationId}/`, {
              data: { delete_stock_items: false, delete_sub_locations: false }
            })
          );

        expect(cleanupFailures).toEqual([]);
      } finally {
        await api.dispose();
      }
    }
  });
}

/**
 * Create a salable part with two stock items, and consume one of them
 * by assigning it to a (new) customer.
 */
const setupConsumedStock = async (api: any) => {
  const create = async (url: string, data: object) => {
    const response = await api.post(url, { data });
    expect(response.status(), await response.text()).toBe(201);
    const body = await response.json();
    return Array.isArray(body) ? body[0] : body;
  };

  const uid = generateUUID().slice(0, 8);

  const part = await create('part/', {
    name: `Consumed part ${uid}`,
    component: true,
    salable: true
  });

  const customer = await create('company/', {
    name: `Consuming customer ${uid}`,
    is_customer: true
  });

  const consumed = await create('stock/', {
    part: part.pk,
    quantity: 1,
    batch: `CONSUMED-${uid}`
  });

  const available = await create('stock/', {
    part: part.pk,
    quantity: 1,
    batch: `AVAILABLE-${uid}`
  });

  const assign = await api.post('stock/assign/', {
    data: { items: [{ item: consumed.pk }], customer: customer.pk }
  });
  expect(assign.ok(), await assign.text()).toBeTruthy();

  const cleanup = async () => {
    for (const pk of [consumed.pk, available.pk])
      await api.delete(`stock/${pk}/`);
    await api.delete(`company/${customer.pk}/`);
    await api.patch(`part/${part.pk}/`, { data: { active: false } });
    await api.delete(`part/${part.pk}/`);
  };

  return { part, customer, consumed, available, cleanup };
};

test('Stock - Consumed badge', async ({ browser }) => {
  const api = await createApi({});
  const { customer, consumed, available, cleanup } =
    await setupConsumedStock(api);

  try {
    const page = await doCachedLogin(browser, {
      url: `stock/item/${consumed.pk}/details`
    });

    // The badge describes the consumer in its tooltip
    const badge = page.getByText('Consumed', { exact: true });
    await expect(badge).toBeVisible();
    await badge.hover();
    await expect(
      page.getByText(
        `This stock item has been assigned to a customer: ${customer.name}`
      )
    ).toBeVisible();

    // An available stock item has no "consumed" badge
    await navigate(page, `stock/item/${available.pk}/details`);
    await expect(
      page.getByText(`${available.batch}`, { exact: false }).first()
    ).toBeVisible();
    await expect(page.getByText('Consumed', { exact: true })).toHaveCount(0);
  } finally {
    await cleanup();
    await api.dispose();
  }
});

test('Stock - Consumed By column', async ({ browser }) => {
  const api = await createApi({});
  const { part, customer, consumed, available, cleanup } =
    await setupConsumedStock(api);

  try {
    const page = await doCachedLogin(browser, {
      url: `part/${part.pk}/stock`
    });

    // Show consumed stock too
    await clearTableFilters(page);
    await expect(
      page.getByRole('cell', { name: consumed.batch, exact: true })
    ).toBeVisible();

    // The column is available, but hidden by default
    const header = page.getByRole('columnheader', {
      name: 'Consumed By',
      exact: true
    });
    await expect(header).toHaveCount(0);

    const toggleColumn = async (show: boolean) => {
      await page.getByLabel('table-select-columns').click();
      const checkbox = page.getByRole('checkbox', {
        name: 'Consumed By',
        exact: true
      });
      await (show ? checkbox.check() : checkbox.uncheck());
      await page.keyboard.press('Escape');
    };

    await toggleColumn(true);
    await expect(header).toBeVisible();

    // The consumed item links to its consumer
    const row = page.getByRole('row').filter({
      has: page.getByRole('cell', { name: consumed.batch, exact: true })
    });
    await expect(
      row.getByRole('link', { name: customer.name, exact: true })
    ).toHaveAttribute('href', `/web/company/${customer.pk}/`);

    // The available item has no consumer
    const availableRow = page.getByRole('row').filter({
      has: page.getByRole('cell', { name: available.batch, exact: true })
    });
    await expect(
      availableRow.getByRole('link', { name: customer.name })
    ).toHaveCount(0);

    // Restore the default column visibility
    await toggleColumn(false);
    await expect(header).toHaveCount(0);
  } finally {
    await cleanup();
    await api.dispose();
  }
});
