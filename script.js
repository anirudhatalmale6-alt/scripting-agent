import http from "k6/http";
import { sleep } from "k6";

const SITE = __ENV.SFCC_SITE_URL;

export const options = {
  vus: 20,
  duration: "30s",
};

export default function () {
  http.get(SITE);
  sleep(1);
}