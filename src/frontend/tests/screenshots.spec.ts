import { test } from './baseFixtures.js';
import { baseUrl } from './defaults.js';
import { doQuickLogin } from './login.js';

/*
 * Helper method to generate a screenshot of the current page,
 * and store it in a named subdirectory.
 */
const screenshot = async (page, prefix, name) => {
  await page.screenshot({
    path: `screenshots/${prefix}/${prefix}_${name}.png`
  });
};

/*
 * Take screenshots of various pages in the user interface,
 * to be used in the documentation.
 */
test('PUI - Screenshots - Build Order', async ({ page }) => {
  await doQuickLogin(page);

  await page.goto(`${baseUrl}/build/`);
  await page.getByText('Widget Board (assembled)').first().waitFor();

  // Close the "logged in" dialog
  await page.locator('#login').getByRole('button').click();

  // Screenshot of build index page
  await screenshot(page, 'build', 'index');

  // Screenshot of "table filters" drawer
  await page.getByLabel('table-select-filters').click();
  await page.getByRole('button', { name: 'Add Filter' }).click();
  await page.getByPlaceholder('Select filter').click();
  await page.getByText('Has Project Code').waitFor();

  await screenshot(page, 'build', 'index_filters');

  // Close the drawer
  await page.getByLabel('filter-drawer-close').click();

  // Navigate to a particular build order
  await page.getByRole('cell', { name: 'BO0010' }).click();

  // Build details tab
  await page.getByRole('tab', { name: 'Build Details' }).click();
  await page
    .getByLabel('Build Details')
    .getByText('Making a high level assembly')
    .waitFor();
  await screenshot(page, 'build', 'details');

  // Line items tab
  await page.getByRole('tab', { name: 'Line Items' }).click();
  await page.getByRole('cell', { name: '1551AGY' }).waitFor();
  await screenshot(page, 'build', 'line_items');

  // Incomplete Outputs Tab
  await page.getByRole('tab', { name: 'Incomplete Outputs' }).click();
  await page.getByText('Batch: 2024-5-').nth(1).waitFor();
  await screenshot(page, 'build', 'incomplete_outputs');

  // Completed Outputs Tab
  await page.getByRole('tab', { name: 'Completed Outputs' }).click();
  await page.getByRole('cell', { name: 'High level assembly of' }).waitFor();
  await screenshot(page, 'build', 'completed_outputs');

  // Allocated stock tab
  await page.getByRole('tab', { name: 'Allocated Stock' }).click();
  await page.getByText('Available Quantity').waitFor();
  await screenshot(page, 'build', 'allocated_stock');

  // Consumed Stock tab
  await page.getByRole('tab', { name: 'Consumed Stock' }).click();
  await page.getByText('Doohickey').waitFor();
  await screenshot(page, 'build', 'consumed_stock');

  // Child build orders tab
  await page.getByRole('tab', { name: 'Child Build Orders' }).click();
  await page
    .getByRole('cell', { name: 'Required parts for Build' })
    .nth(1)
    .waitFor();
  await screenshot(page, 'build', 'child_build_orders');

  // Test Results tab
  await page.getByRole('tab', { name: 'Test Results' }).click();
  await page.getByText('Fail').waitFor();
  await screenshot(page, 'build', 'test_results');

  // Test statistics tab
  await page.getByRole('tab', { name: 'Test Statistics' }).click();
  await page.getByRole('cell', { name: '(100%)' }).first().waitFor();
  await screenshot(page, 'build', 'test_statistics');

  // Attachments tab
  await page.getByRole('tab', { name: 'Attachments' }).click();
  await page.getByLabel('action-button-add-attachment').waitFor();
  await screenshot(page, 'build', 'attachments');

  // Notes tab
  await page.getByRole('tab', { name: 'Notes' }).click();
  await page.getByLabel('toggle-notes-editing').waitFor();
  await screenshot(page, 'build', 'notes');
});
