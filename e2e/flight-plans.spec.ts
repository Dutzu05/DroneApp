import { expect, test } from '@playwright/test';

function uniqueEmail(prefix: string): string {
  return `${prefix}-${Date.now()}@example.com`;
}

function tomorrowLocalDateString(): string {
  const now = new Date();
  const tomorrow = new Date(now.getTime() + 24 * 60 * 60 * 1000);
  return tomorrow.toISOString().slice(0, 10);
}

test.describe('Critical Flight Plan Journeys', () => {
  test('@compose user can log in through backend session, create a polygon flight plan, and cancel it', async ({ page }) => {
    const email = uniqueEmail('playwright-e2e');
    const flightDate = tomorrowLocalDateString();

    await page.goto('/');
    await expect(page.locator('#authGate')).toBeVisible();

    const loginResponse = await page.context().request.post('/api/auth/google-session', {
      data: {
        email,
        display_name: 'Playwright E2E',
        google_user_id: `gid-${Date.now()}`,
        app: 'playwright_e2e',
      },
    });
    expect(loginResponse.ok()).toBeTruthy();

    await page.goto('/');
    await expect(page.locator('#authGate')).toBeHidden();

    page.on('dialog', dialog => dialog.accept());

    await page.getByRole('button', { name: /new uas notification/i }).click();
    await page.locator('#fpAreaKind').selectOption('polygon');

    const polygonPoints = [
      ['44.430100', '26.103100'],
      ['44.430100', '26.104400'],
      ['44.431100', '26.104400'],
      ['44.431100', '26.103100'],
    ];
    for (const [lat, lon] of polygonPoints) {
      await page.locator('#fpPolyLat').fill(lat);
      await page.locator('#fpPolyLon').fill(lon);
      await page.getByRole('button', { name: /add typed vertex/i }).click();
    }

    await page.locator('#fpAlt').fill('120');
    await page.locator('#fpCheckBtn').click();
    await expect(page.locator('#riskBadge')).toBeVisible();
    await page.getByRole('button', { name: /continue/i }).click();

    await page.locator('#fp_operator').fill('Playwright E2E');
    await page.locator('#fp_address').fill('Bucharest contact');
    await page.locator('#fp_contact_person').fill('Playwright E2E');
    await page.locator('#fp_phone_landline').fill('0210000000');
    await page.locator('#fp_mobil').fill('0711111111');
    await page.locator('#fp_email').fill(email);
    await page.locator('#fp_reg').fill(`RO-UAS-${Date.now()}`);
    await page.locator('#fp_weight').fill('1.2');
    await page.locator('#fp_pilot').fill('Playwright E2E');
    await page.locator('#fp_pphone').fill('0711111111');
    await page.locator('#fp_purpose').fill('Playwright E2E flow');
    await page.locator('#fp_loc').fill('Bucharest Playwright Zone');
    await page.locator('#fp_date1').fill(flightDate);
    await page.locator('#fp_date2').fill(flightDate);
    await page.locator('#fp_time1').fill('09:00');
    await page.locator('#fp_time2').fill('10:00');

    await page.locator('#fpSaveBtn').click();
    await expect(page.locator('#fpSavedSummary')).toContainText('Download ANEXA 1 PDF');
    const createdPlanId = await page.locator('#fpSavedSummary .saved-title').innerText();
    await page.locator('#fpOverlay .close-wiz').click();
    await expect(page.locator('#fpOverlay')).toBeHidden();
    await expect(page.locator('#myPlansList')).toContainText(createdPlanId);

    await page.locator(`#myPlansList button:has-text("Cancel")`).first().click();
    await expect(page.locator('#myPlansList')).toContainText('cancelled');
  });

  test('@staging user can unlock the app shell and reach admin pages', async ({ page }) => {
    const email = 'playwright-staging@example.com';

    const loginResponse = await page.context().request.post('/api/auth/google-session', {
      data: {
        email,
        display_name: 'Playwright Staging',
        google_user_id: 'gid-playwright-staging',
        app: 'playwright_e2e',
      },
    });
    expect(loginResponse.ok()).toBeTruthy();

    await page.goto('/');
    await expect(page.locator('#authGate')).toBeHidden();

    await page.goto('/admin/flight-plans');
    await expect(page.getByRole('heading', { name: /stored flight plans/i })).toBeVisible();

    await page.goto('/admin/logged-accounts');
    await expect(page.getByRole('heading', { name: /logged google accounts/i })).toBeVisible();
  });
});
