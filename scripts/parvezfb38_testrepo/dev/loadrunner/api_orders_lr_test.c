#include "lrun.h"
#include "web_api.h"
#include "lrw_custom_body.h"

vuser_init()
{
    // Initialization code can go here
    return 0;
}

Action()
{
    // Start the transaction for creating a new order
    lr_start_transaction("Create_Order");

    // Register a response check to verify the order creation
    web_reg_find("Text=Order created successfully", "Fail=NotFound", LAST);

    // Submit the order data
    web_submit_data("Create_Order",
        "Action={{{SFCC_SITE_URL}}}/api/orders",
        "Method=POST",
        "RecContentType=application/json",
        "Referer={{{SFCC_SITE_URL}}}/api/orders",
        "Snapshot=t1.inf",
        "Mode=HTTP",
        ITEMDATA,
        "Name=product_id", "Value=12345", ENDITEM,
        "Name=quantity", "Value=2", ENDITEM,
        "Name=customer_name", "Value=John Doe", ENDITEM,
        "Name=customer_email", "Value=johndoe@example.com", ENDITEM,
        "Name=shipping_address", "Value=123 Main St, Anytown, USA", ENDITEM,
        LAST);

    // End the transaction
    lr_end_transaction("Create_Order", LR_AUTO);

    // Think time to simulate user delay
    lr_think_time(1);

    return 0;
}

vuser_end()
{
    // Cleanup code can go here
    return 0;
}