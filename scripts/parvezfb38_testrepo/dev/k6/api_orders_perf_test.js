import http from 'k6/http';
import { check, sleep, group } from 'k6';

const BASE_URL = __ENV.SFCC_SITE_URL || 'https://test.k6.io';
const IS_REAL_APP = !BASE_URL.includes('test.k6.io');

export const options = {
    vus: 10,
    duration: '30s',
    thresholds: {
        'http_req_duration': ['p(95)<2000'],
        'http_req_failed': ['rate<0.05'], // Changed to http_req_failed for error rate
    },
};

const createOrder = () => {
    const orderData = {
        customerId: '12345',
        items: [
            { productId: 'abc123', quantity: 2 },
            { productId: 'xyz789', quantity: 1 },
        ],
        shippingAddress: {
            street: '123 Main St',
            city: 'Anytown',
            state: 'CA',
            zip: '12345',
            country: 'USA',
        },
        paymentMethod: {
            type: 'credit_card',
            cardNumber: '4111111111111111',
            expirationDate: '12/25',
            cvv: '123',
        },
    };

    const url = `${BASE_URL}/api/orders`;
    const params = {
        headers: {
            'Content-Type': 'application/json',
        },
    };

    if (IS_REAL_APP) {
        const response = http.post(url, JSON.stringify(orderData), params);
        check(response, {
            'is status 200 or 201': (r) => r.status === 200 || r.status === 201,
        });
    } else {
        http.get(BASE_URL);
    }
};

export default function () {
    group('Create Order Transaction', () => {
        createOrder();
        sleep(1);
    });
}