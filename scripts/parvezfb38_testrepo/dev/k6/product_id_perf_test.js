import http from 'k6/http';
import { check, sleep, group } from 'k6';

const BASE_URL = __ENV.SFCC_SITE_URL || 'https://test.k6.io';
const IS_REAL_APP = !BASE_URL.includes('test.k6.io');

export const options = {
    vus: 1, // Set to 1 to limit the number of concurrent users
    duration: '30s',
    thresholds: {
        'http_req_duration': ['p(95)<2000'],
        'http_reqs': ['rate<0.05'], // Keep this threshold as is
    },
};

export default function () {
    const product_id = Math.floor(Math.random() * 1000); // Simulating a random product_id
    const url = `${BASE_URL}/${product_id}`;
    const params = { headers: { 'Content-Type': 'application/json' } };

    group('DELETE /{product_id}', function () {
        let response;
        if (IS_REAL_APP) {
            response = http.del(url, null, params);
        } else {
            response = http.get(BASE_URL);
        }

        check(response, {
            'is status 200 or 204': (r) => r.status === 200 || r.status === 204,
        });

        sleep(20); // Increase sleep time to reduce request rate
    });
}