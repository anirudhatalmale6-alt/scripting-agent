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
    // Start transaction for adding a new user
    lr_start_transaction("Add_New_User");

    // Register to find a specific response content to validate the request
    web_reg_find("Text=User created successfully", "Fail=NotFound", LAST);

    // Submit data to add a new user
    web_submit_data("api/users",
        "Action={{{SFCC_SITE_URL}}}/api/users",
        "Method=POST",
        "RecContentType=application/json",
        "Referer={{{SFCC_SITE_URL}}}/api/users",
        "Snapshot=t1.inf",
        "Mode=HTML",
        ITEMDATA,
        "Name=username", "Value=testuser", ENDITEM,
        "Name=password", "Value=Test@123", ENDITEM,
        "Name=email", "Value=testuser@example.com", ENDITEM,
        LAST);

    // End transaction for adding a new user
    lr_end_transaction("Add_New_User", LR_AUTO);

    // Think time after the transaction
    lr_think_time(1);

    return 0;
}

vuser_end()
{
    // Cleanup code can go here
    return 0;
}