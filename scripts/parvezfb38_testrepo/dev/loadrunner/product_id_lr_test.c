#include "lrun.h"
#include "web_api.h"
#include "lrw_custom_body.h"

#define BASE_URL "https://localhost:8000"

// Function prototypes
int vuser_init();
int Action();
int vuser_end();

vuser_init()
{
    // Initialization code can be added here if needed
    return 0;
}

Action()
{
    char product_id[10];

    // Generate a sample product_id for testing
    sprintf(product_id, "%d", rand() % 1000); // Random product_id between 0 and 999

    // Start transaction for DELETE request
    lr_start_transaction("DELETE_Remove_Product");

    // Register to find a specific content in the response
    web_reg_find("Text=Product deleted successfully", "Fail=NotFound", LAST);

    // Perform the DELETE request
    web_custom_request("Remove_Product",
        "URL=" BASE_URL "/{product_id}",
        "Method=DELETE",
        "Resource=0",
        "RecContentType=application/json",
        "Referer=" BASE_URL,
        "Snapshot=t1.inf",
        "Mode=HTTP",
        LAST);

    // End transaction for DELETE request
    lr_end_transaction("DELETE_Remove_Product", LR_AUTO);

    // Think time after transaction
    lr_think_time(1);

    return 0;
}

vuser_end()
{
    // Cleanup code can be added here if needed
    return 0;
}