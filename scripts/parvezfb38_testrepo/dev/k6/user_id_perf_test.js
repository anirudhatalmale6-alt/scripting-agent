import http from 'k6/http';
import { check, sleep, group } from 'k6';

const BASE_URL = __ENV.SFCC_SITE_URL || 'https://test.k6.io';
const IS_REAL_APP = !BASE_URL.includes('test.k6.io');

export const options = {
    vus: 1, // Number of virtual users
    duration: '30s', // Duration of the test
    thresholds: {
        'http_req_duration': ['p(95)<2000'], // 95th percentile response time should be less than 2000ms
        'http_reqs': ['rate<1'], // Allow for less than 1 request per second
    },
};

export default function () {
    group('DELETE /{user_id}', function () {
        const userId = Math.floor(Math.random() * 1000); // Simulating user IDs from 0 to 999
        const url = `${BASE_URL}/${userId}`;

        let response;
        if (IS_REAL_APP) {
            response = http.del(url);
        } else {
            response = http.get(BASE_URL);
        }

        check(response, {
            'is status 200 or 204': (r) => r.status === 200 || r.status === 204,
        });

        sleep(1); // Sleep for 1 second to control the request rate
    });
}