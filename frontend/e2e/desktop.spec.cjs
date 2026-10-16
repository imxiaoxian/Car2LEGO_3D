const { test, expect } = require('@playwright/test');
const { PNG } = require('pngjs');
const path = require('node:path');

const job = process.env.DESKTOP_JOB;

test('original creation, brand selection, optional year, and photo workflow', async ({ page }, info) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Car2LEGO', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '选择品牌', exact: true }).click();
  await page.getByRole('textbox', { name: '搜索品牌', exact: true }).fill('宝马');
  await page.getByRole('button', { name: '宝马 BMW', exact: true }).click();
  await expect(page.getByRole('button', { name: '选择品牌：宝马' })).toBeVisible();
  await expect(page.getByRole('spinbutton', { name: '年款（选填）' })).toHaveValue('');
  await page.getByRole('button', { name: '选择品牌：宝马' }).click();
  await page.getByRole('button', { name: '其他，自定义品牌' }).click();
  await page.getByRole('textbox', { name: '自定义品牌名称' }).fill('测试品牌');
  await page.getByRole('textbox', { name: '车型', exact: true }).fill('Test');
  await expect(page.getByRole('combobox', { name: /比例/ })).toHaveCount(0);
  await expect(page.getByRole('region', { name: '比例设计要求' })).toHaveCount(0);
  await page.screenshot({ path: info.outputPath('creation.png') });
  await page.getByRole('button', { name: '车辆照片', exact: true }).click();
  await page.getByLabel('上传车辆照片').setInputFiles(path.join(__dirname, '../public/images/ferrari-f40.webp'));
  await expect(page.getByAltText('已选择的车辆照片')).toBeVisible();
  await page.getByRole('button', { name: '移除照片' }).click();
  await expect(page.getByRole('button', { name: '选择车辆照片' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
});

test('vehicle catalog filters and prepopulates creation', async ({ page }) => {
  await page.goto('/sets');
  await page.getByRole('combobox', { name: '筛选品牌' }).selectOption('BMW');
  await expect(page.getByRole('heading', { name: 'BMW M3 E30' })).toBeVisible();
  await expect(page.locator('.catalog-item')).toHaveCount(1);
  await expect(page.getByRole('combobox', { name: /比例/ })).toHaveCount(0);
  await page.getByRole('link', { name: '创建设计', exact: true }).click();
  await expect(page.getByRole('textbox', { name: '车型', exact: true })).toHaveValue('M3 E30');
});

test('real draft geometry, canvas pixels, controls, and exports', async ({ page, request }, info) => {
  test.skip(!job, 'Set DESKTOP_JOB to a real imported or generated draft');
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(`/?job=${job}`);
  await expect(page).toHaveURL(new RegExp(`/designs/${job}$`));
  await expect(page.getByRole('button', { name: '恢复视角' })).toBeVisible({ timeout: 60000 });
  const canvas = page.locator('.assembly-canvas canvas');
  await canvas.scrollIntoViewIfNeeded();
  const first = await canvas.screenshot({ path: info.outputPath('assembly.png') });
  const pixels = PNG.sync.read(first);
  const colors = new Set();
  for (let i = 0; i < pixels.data.length; i += 16) colors.add(pixels.data.readUInt32BE(i));
  expect(colors.size).toBeGreaterThan(100);
  await page.getByRole('button', { name: '线框', exact: true }).click();
  await expect(page.getByRole('button', { name: '线框', exact: true })).toHaveAttribute('aria-pressed', 'true');
  const wire = await canvas.screenshot({ path: info.outputPath('wireframe.png') });
  expect(first.equals(wire)).toBeFalsy();
  await page.getByRole('button', { name: '线框', exact: true }).click();
  const box = await canvas.boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + 150, box.y + box.height / 2 + 60, { steps: 15 });
  await page.mouse.up();
  const rotated = await canvas.screenshot({ path: info.outputPath('rotated.png') });
  expect(rotated.equals(first)).toBeFalsy();
  await page.getByRole('button', { name: '恢复视角' }).click();
  await page.getByRole('slider', { name: '搭建步骤' }).focus();
  await page.keyboard.press('Home');
  await expect(page.getByRole('slider', { name: '搭建步骤' })).toHaveValue('1');
  const partial = await canvas.screenshot({ path: info.outputPath('first-step.png') });
  expect(partial.equals(first)).toBeFalsy();
  await page.keyboard.press('End');
  const response = await request.get(`/api/v1/designs/${job}`);
  const detail = await response.json();
  expect(detail.status).toBe('draft');
  expect(detail.quality_report.precision_verified).toBe(false);
  for (const [kind, url] of Object.entries(detail.file_urls)) {
    const exported = await request.get(url);
    expect(exported.ok(), kind).toBeTruthy();
    expect((await exported.body()).length, kind).toBeGreaterThan(30);
  }
  await page.getByRole('button', { name: '放大参考图' }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.getByRole('button', { name: '关闭预览' }).click();
  await page.getByRole('textbox', { name: '定制内容' }).fill('车身改为黑色');
  await expect(page.getByRole('button', { name: '创建设计副本' })).toBeEnabled();
  expect(errors).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
});
