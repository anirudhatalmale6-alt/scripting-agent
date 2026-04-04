import http from 'k6/http';
import { check, sleep, group } from 'k6';

const BASE_URL = __ENV.SFCC_SITE_URL || 'https://test.k6.io';
const IS_REAL_APP = !BASE_URL.includes('test.k6.io');

export const options = {
    vus: 10,
    duration: '30s',
    thresholds: {
        'http_req_duration': ['p(95)<2000'], // 95% of requests must complete below 2000ms
        'http_req_failed': ['rate<0.05'], // 5% of requests can fail
    },
};

export default function () {
    group('User Login', function () {
        let loginResponse;
        if (IS_REAL_APP) {
            loginResponse = http.post(`${BASE_URL}/login.pl`, {
                username: 'jojo',
                password: 'bean',
            });
        } else {
            loginResponse = http.get(BASE_URL);
        }

        check(loginResponse, {
            'login successful': (r) => r.status === 200 || r.status === 201,
        });

        sleep(1);
    });

    group('Access Inventory', function () {
        let inventoryResponse;
        if (IS_REAL_APP) {
            inventoryResponse = http.get(`${BASE_URL}/inventory.html`);
        } else {
            inventoryResponse = http.get(BASE_URL);
        }

        check(inventoryResponse, {
            'inventory page loaded': (r) => r.status === 200 || r.status === 201,
        });

        sleep(1);
    });

    group('Access Cart', function () {
        let cartResponse;
        if (IS_REAL_APP) {
            cartResponse = http.get(`${BASE_URL}/cart.html`);
        } else {
            cartResponse = http.get(BASE_URL);
        }

        check(cartResponse, {
            'cart page loaded': (r) => r.status === 200 || r.status === 201,
        });

        sleep(1);
    });

    group('Checkout Step One', function () {
        let checkoutStepOneResponse;
        if (IS_REAL_APP) {
            checkoutStepOneResponse = http.get(`${BASE_URL}/checkout-step-one.html`);
        } else {
            checkoutStepOneResponse = http.get(BASE_URL);
        }

        check(checkoutStepOneResponse, {
            'checkout step one loaded': (r) => r.status === 200 || r.status === 201,
        });

        sleep(1);
    });

    group('Checkout Step Two', function () {
        let checkoutStepTwoResponse;
        if (IS_REAL_APP) {
            checkoutStepTwoResponse = http.get(`${BASE_URL}/checkout-step-two.html`);
        } else {
            checkoutStepTwoResponse = http.get(BASE_URL);
        }

        check(checkoutStepTwoResponse, {
            'checkout step two loaded': (r) => r.status === 200 || r.status === 201,
        });

        sleep(1);
    });

    group('Checkout Complete', function () {
        let checkoutCompleteResponse;
        if (IS_REAL_APP) {
            checkoutCompleteResponse = http.get(`${BASE_URL}/checkout-complete.html`);
        } else {
            checkoutCompleteResponse = http.get(BASE_URL);
        }

        check(checkoutCompleteResponse, {
            'checkout complete loaded': (r) => r.status === 200 || r.status === 201,
        });

        sleep(1);
    });
}