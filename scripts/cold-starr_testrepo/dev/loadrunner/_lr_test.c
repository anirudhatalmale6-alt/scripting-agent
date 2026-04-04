#include "lrun.h"
#include "web_api.h"
#include "lrw_custom_body.h"

#define BASE_URL "{{SFCC_SITE_URL}}"

vuser_init()
{
    // Initialization code can go here
    return 0;
}

Action()
{
    // Transaction for SauceDemo login
    lr_start_transaction("SauceDemo_Login");

    web_reg_save_param("ParamName=sessionId", "LB=sessionId=", "RB=;", LAST);
    
    web_submit_data("login",
        "Action=" BASE_URL "/login",
        "Method=POST",
        "RecContentType=text/html",
        "Referer=" BASE_URL "/",
        "Snapshot=t1.inf",
        "Mode=HTML",
        ITEMDATA,
        "Name=username", "Value=standard_user", ENDITEM,
        "Name=password", "Value=secret_sauce", ENDITEM,
        LAST);

    lr_end_transaction("SauceDemo_Login", LR_AUTO);
    lr_think_time(1);

    // Transaction for accessing inventory page
    lr_start_transaction("SauceDemo_View_Inventory");

    web_reg_find("Text=Products", LAST);

    web_url("inventory",
        "URL=" BASE_URL "/inventory.html",
        "Resource=0",
        "RecContentType=text/html",
        "Referer=" BASE_URL "/",
        "Snapshot=t2.inf",
        "Mode=HTML",
        LAST);

    lr_end_transaction("SauceDemo_View_Inventory", LR_AUTO);
    lr_think_time(1);

    // Transaction for WebTours login
    lr_start_transaction("WebTours_Login");

    web_reg_save_param("ParamName=sessionToken", "LB=sessionToken=", "RB=;", LAST);

    web_submit_data("login.pl",
        "Action=http://localhost:1080/WebTours/login.pl",
        "Method=POST",
        "RecContentType=text/html",
        "Referer=http://localhost:1080/WebTours/",
        "Snapshot=t3.inf",
        "Mode=HTML",
        ITEMDATA,
        "Name=username", "Value=jojo", ENDITEM,
        "Name=password", "Value=bean", ENDITEM,
        LAST);

    lr_end_transaction("WebTours_Login", LR_AUTO);
    lr_think_time(1);

    // Transaction for accessing flights page
    lr_start_transaction("WebTours_View_Flights");

    web_reg_find("Text=Available Flights", LAST);

    web_url("flights.pl",
        "URL=http://localhost:1080/WebTours/flights.pl",
        "Resource=0",
        "RecContentType=text/html",
        "Referer=http://localhost:1080/WebTours/",
        "Snapshot=t4.inf",
        "Mode=HTML",
        LAST);

    lr_end_transaction("WebTours_View_Flights", LR_AUTO);
    lr_think_time(1);

    return 0;
}

vuser_end()
{
    // Cleanup code can go here
    return 0;
}