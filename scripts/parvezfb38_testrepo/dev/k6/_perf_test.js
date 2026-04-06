import http from 'k6/http';
import { check, sleep, group } from 'k6';

export const options = {
    vus: 10,
    duration: '30s',
    thresholds: {
        http_req_duration: ['p(95)<2000'], // 95% of requests must complete below 2000ms
        http_req_failed: ['rate<0.05'], // <5% of requests should fail
    },
};

const BASE_URL = __ENV.SFCC_SITE_URL || 'https://test.k6.io';
const IS_REAL_APP = !BASE_URL.includes('test.k6.io');

export default function () {
    group('POST /', function () {
        let response;
        const payload = JSON.stringify({ key: 'value' }); // Adjust payload as needed
        const params = {
            headers: {
                'Content-Type': 'application/json',
            },
        };

        if (IS_REAL_APP) {
            response = http.post(`${BASE_URL}/`, payload, params);
        } else {
            response = http.post(BASE_URL, payload, params);
        }

        check(response, {
            'is status 200 or 201': (r) => r.status === 200 || r.status === 201,
        });

        sleep(1);
    });
}