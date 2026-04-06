import http from 'k6/http';
import { check, sleep, group } from 'k6';

export const options = {
  vus: 10,
  duration: '30s',
  thresholds: {
    http_req_duration: ['p(95)<2000'],
    http_req_failed: ['rate<0.05'],
  },
};

const BASE_URL = __ENV.SFCC_SITE_URL || 'https://test.k6.io';
const IS_REAL_APP = !BASE_URL.includes('test.k6.io');

export default function () {
    group('Add New User', function () {
        const url = `${BASE_URL}/api/users`;
        const payload = JSON.stringify({
            name: 'John Doe',
            email: 'john.doe@example.com',
            password: 'securePassword123',
        });

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