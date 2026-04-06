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
    // Initialization code can go here if needed
    return 0;
}

Action()
{
    char order_id[10];
    
    // Simulate getting an order ID (this could be a correlation from a previous response)
    // For demonstration, we will use a static order ID
    strcpy(order_id, "12345");

    // Start transaction for DELETE request
    lr_start_transaction("Delete_Order");

    // Register to find specific content in the response
    web_reg_find("Text=Order deleted successfully", "Fail=NotFound", LAST);

    // Make the DELETE request
    web_url("Delete_Order",
        "URL=" BASE_URL "/" order_id,
        "Resource=0",
        "RecContentType=application/json",
        "Referer=" BASE_URL,
        "Snapshot=t1.inf",
        "Mode=HTTP",
        "Method=DELETE",
        LAST);

    // End transaction
    lr_end_transaction("Delete_Order", LR_AUTO);

    // Think time after the transaction
    lr_think_time(1);

    return 0;
}

vuser_end()
{
    // Cleanup code can go here if needed
    return 0;
}