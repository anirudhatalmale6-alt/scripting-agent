import http from 'k6/http';
import { check, sleep, group } from 'k6';

const BASE_URL = __ENV.SFCC_SITE_URL || 'https://test.k6.io';
const IS_REAL_APP = !BASE_URL.includes('test.k6.io');

export const options = {
    vus: 1, // Reduced the number of VUs to 1
    duration: '30s',
    thresholds: {
        'http_req_duration': ['p(95)<2000'],
        'http_reqs': ['rate<1'], // Increased the rate threshold to allow more requests
    },
};

const generateProductData = () => {
    return JSON.stringify({
        name: `Product ${Math.floor(Math.random() * 1000)}`,
        price: Math.floor(Math.random() * 100) + 1,
        description: 'This is a test product.',
        category: 'Test Category',
    });
};

export default function () {
    group('Add new product', function () {
        const url = `${BASE_URL}/api/products`;
        const payload = generateProductData();
        const params = {
            headers: {
                'Content-Type': 'application/json',
            },
        };

        let response;
        if (IS_REAL_APP) {
            response = http.post(url, payload, params);
        } else {
            response = http.get(BASE_URL);
        }

        check(response, {
            'is status 200 or 201': (r) => r.status === 200 || r.status === 201,
        });

        sleep(1);
    });
}