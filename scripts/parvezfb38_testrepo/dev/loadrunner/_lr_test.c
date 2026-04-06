#include "lrun.h"
#include "web_api.h"
#include "lrw_custom_body.h"

#define BASE_URL "https://localhost:8000"

vuser_init()
{
   
    return 0;
}

Action()
{
    // Start transaction for the POST request
    lr_start_transaction("POST_Root_Endpoint");

    // Set up the request body for the POST request
    char *requestBody = "{\"key1\":\"value1\", \"key2\":\"value2\"}";

    // Register a response check to verify that the POST request is successful
    web_reg_find("Text=Success", "Last");

    // Perform the POST request to the root endpoint
    web_custom_request("Post_Root",
        "URL=" BASE_URL "/",
        "Method=POST",
        "Resource=0",
        "RecContentType=application/json",
        "Referer=" BASE_URL "/",
        "Body={requestBody}",
        "Snapshot=t1.inf",
        "Mode=HTTP",
        LAST);

    // End the transaction for the POST request
    lr_end_transaction("POST_Root_Endpoint", LR_AUTO);

    // Think time after the transaction
    lr_think_time(1);

    return 0;
}

vuser_end()
{
    // Cleanup code can be added here if needed
    return 0;
}